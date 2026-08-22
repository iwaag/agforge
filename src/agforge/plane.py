"""Where agforge's create topics land in Plane.

The client itself is `agag.plane`, shared with agautolab. What lives here is
only agforge's policy on top of it: which project a channel routes to, and
the two markers that make a Work executable by agforge's own `assetrun-`
flow while staying invisible to agautolab's.

Every Work registered here carries the `FORGEAUTO` label, and the fallback
`FreeForge` project carries the `[AUTO]` description marker. The `[AUTO]`
marker is what work selection scans (agforge's and autolab's alike); the
*label* is what keeps the two agents apart — autolab's `next_work` executes
only `AUTO`-labelled issues, agforge's only `FORGEAUTO`-labelled ones.
(This reverses the p1 "no labels, no `[AUTO]`" decision on purpose; see
`devdocs/episodes/agforge/modernize/p2/plan.md`.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agag import plane as shared
from agag.plane import (
    PlaneError,
    create_label,
    create_project,
    description_html,
    ensure_issue,
    find_issue_by_external,
    find_project,
    issue_label,
    labels_by_name,
    load_plane_config as load_shared_plane_config,
    split_document,
    starting_state_id,
    update_issue,
)

from agag.plane import credentials_path

from .role_run import AGFORGE_ROOT

# `AGAG_PLANE_ENV`, else `.local/plane-credentials.env` beside this agent
# (the shared pj-agdev file is symlinked there).
PLANE_ENV = credentials_path(AGFORGE_ROOT)

EXTERNAL_SOURCE = "agforge"
PROJECT_CHANNEL_PREFIX = "pj-"
FALLBACK_PROJECT = "FreeForge"
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")

# The project-description marker work selection scans for, shared vocabulary
# with autolab's `project_slug`, and the label that makes a Work agforge's to
# execute (autolab's is `AUTO`).
AUTO_MARKER = "[AUTO]"
FORGE_LABEL = "FORGEAUTO"

# The third marker: which toolsets this Work was planned with, carried as the
# description's last line so `assetrun` can rebuild the same `tools/`. It
# rides in the description rather than a comment because `next_work` already
# reads the description, and comments would need a new list API in agag.plane.
TOOLS_MARKER = "[TOOLS]"
FALLBACK_DESCRIPTION = f"{AUTO_MARKER} agforge request records: {FALLBACK_PROJECT}"

_LABEL_CACHE: dict[tuple[str, str], str] = {}

__all__ = [
    "AUTO_MARKER",
    "Registration",
    "FORGE_LABEL",
    "TOOLS_MARKER",
    "PlaneError",
    "ensure_label",
    "external_id",
    "register_plan",
    "resolve_project",
    "split_tools_footer",
    "with_tools_footer",
]


def with_tools_footer(description: str, names) -> str:
    """The description plus its `[TOOLS] a, b` last line.

    No toolsets, no line: a Work planned without any is indistinguishable
    from a hand-made one, and both are answered the same way — with the
    whole library.
    """
    listed = [str(name).strip() for name in names if str(name).strip()]
    if not listed:
        return description
    footer = f"{TOOLS_MARKER} {', '.join(listed)}"
    body = description.rstrip()
    return f"{body}\n{footer}" if body else footer


def split_tools_footer(description: str) -> tuple[str, list[str] | None]:
    """`(description without the footer, the names)`, or `(description, None)`.

    `None` is not the same as `[]`: it says this Work carries no footer at
    all — hand-made, or from before this phase — and the caller answers that
    with every toolset rather than with none.
    """
    kept: list[str] = []
    names: list[str] | None = None
    for line in description.splitlines():
        if line.strip().upper().startswith(TOOLS_MARKER):
            tail = line.strip()[len(TOOLS_MARKER):]
            names = [part.strip() for part in tail.split(",") if part.strip()]
            continue
        kept.append(line)
    return "\n".join(kept).strip(), names


def load_plane_config(path: Path | None = None):
    return load_shared_plane_config(path or PLANE_ENV)


def external_id(channel: str, topic: str) -> str:
    """One topic, one key — however far the generation number climbs."""
    return f"{channel}/{topic}"


def ensure_label(config, project_id: str, name: str = FORGE_LABEL) -> str:
    """The project's label id for `name`, creating the label on first use.

    Cached per (project, name) for the process: labels are created once and
    never renamed, and this is called for every issue agforge writes.
    """
    key = (project_id, name.lower())
    if key in _LABEL_CACHE:
        return _LABEL_CACHE[key]
    existing = labels_by_name(config, project_id)
    if name.lower() not in existing:
        if created := create_label(config, project_id, name):
            _LABEL_CACHE[key] = created
            return created
        existing = labels_by_name(config, project_id)  # lost a race, or name taken
    if name.lower() not in existing:
        raise PlaneError(f"Plane project has no label {name!r} and it could not be created")
    _LABEL_CACHE[key] = existing[name.lower()]
    return _LABEL_CACHE[key]


def _update_project(config, project_id: str, body: dict) -> None:
    """PATCH one project. `agag.plane` has no project update; the shared
    module's HTTP shape is reused rather than re-spelled."""
    status, payload = shared._request_json(
        "PATCH", f"{shared._project_base(config, project_id)}/",
        headers=shared._headers(config), body=body, timeout=60,
    )
    if status != 200:
        raise PlaneError(f"Plane project update returned HTTP {status}: {payload!r}")


def _fallback(config) -> dict:
    """The `FreeForge` project, created — or reconciled — to carry `[AUTO]`.

    The project predates this marker (p1 deliberately omitted it), and
    `create_project` only sets the description at creation, so an existing
    project's description is patched here instead of by hand.
    """
    if project := find_project(config, FALLBACK_PROJECT):
        description = str(project.get("description") or "").strip()
        if not description.upper().startswith(AUTO_MARKER):
            _update_project(config, str(project["id"]), {"description": FALLBACK_DESCRIPTION})
            project = {**project, "description": FALLBACK_DESCRIPTION}
        return project
    return create_project(config, FALLBACK_PROJECT, FALLBACK_DESCRIPTION)


def resolve_project(config, channel: str) -> tuple[dict, str | None]:
    """Where a channel's Works live: `(project, note)`.

    `pj-<name>` routes to the Plane project of that name. Anything else — and
    a `pj-` channel whose project does not exist — routes to `FreeForge`,
    created on first use. A fallback is reported in `note`, not raised: an
    unregistered project name is a routing fact, not a failure.
    """
    if not channel.startswith(PROJECT_CHANNEL_PREFIX):
        return _fallback(config), None
    name = channel.removeprefix(PROJECT_CHANNEL_PREFIX)
    if PROJECT_NAME.fullmatch(name):
        if project := find_project(config, name):
            return project, None
        note = f"no Plane project named {name!r}; registering in {FALLBACK_PROJECT} instead"
    else:
        note = (
            f"{channel!r} does not carry a valid project name; "
            f"registering in {FALLBACK_PROJECT} instead"
        )
    return _fallback(config), note


@dataclass(frozen=True)
class Registration:
    """One registered Work: what to say about it, and how to name it again.

    The identity is returned, not only the sentence, because since p8 the
    caller opens the Work's own `assetrun-` topic and anchors it to these
    two ids — a topic that says what it runs instead of a queue that guesses.
    """

    line: str
    project_id: str
    issue_id: str
    title: str
    label: str


def register_plan(channel: str, topic: str, plan: Path, tools=()) -> Registration:
    """Register one generator `plan.md` as this topic's Plane Work.

    `tools` are the toolsets this generation actually placed in `tools/`;
    they travel to the executing run as the description's `[TOOLS]` footer.
    Updating through the same external key is what keeps one topic to one
    Work.
    """
    title, description = split_document(plan.read_text(encoding="utf-8"))
    description = with_tools_footer(description, tools)
    config = load_plane_config()
    project, note = resolve_project(config, channel)
    project_id = str(project["id"])
    key = external_id(channel, topic)
    if existing := find_issue_by_external(config, project_id, EXTERNAL_SOURCE, key):
        update_issue(
            config, project_id, str(existing["id"]),
            {"name": title.strip(), "description_html": description_html(description)},
        )
        issue = existing
        line = f'updated {issue_label(project, existing)} "{title}"'
    else:
        issue, _ = ensure_issue(
            config,
            project_id,
            name=title,
            description=description,
            state=starting_state_id(config, project_id),
            external_source=EXTERNAL_SOURCE,
            external_id=key,
            # The label is what `assetrun-` work selection picks up — and
            # what autolab's `next_work` (which wants `AUTO`) passes over.
            labels=[ensure_label(config, project_id)],
        )
        line = f'created {issue_label(project, issue)} "{title}"'
    line += f" in {project.get('name', '?')}"
    return Registration(
        line=f"{note}\n{line}" if note else line,
        project_id=project_id,
        issue_id=str(issue["id"]),
        title=title.strip(),
        label=issue_label(project, issue),
    )
