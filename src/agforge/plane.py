"""Where agforge's create topics land in Plane.

The client itself is `agag.plane`, shared with agautolab. What lives here is
only agforge's policy on top of it: which project a channel routes to, and
the two markers that make a Work executable by agforge's own `runcreate-`
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

from .role_run import AGFORGE_ROOT

# agforge's root has the same parent as agautolab's, so the credentials file
# both agents share is reached by the same expression.
PLANE_ENV = AGFORGE_ROOT.parent / ".local" / "plane-credentials.env"

EXTERNAL_SOURCE = "agforge"
PROJECT_CHANNEL_PREFIX = "pj-"
FALLBACK_PROJECT = "FreeForge"
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")

# The project-description marker work selection scans for, shared vocabulary
# with autolab's `project_slug`, and the label that makes a Work agforge's to
# execute (autolab's is `AUTO`).
AUTO_MARKER = "[AUTO]"
FORGE_LABEL = "FORGEAUTO"
FALLBACK_DESCRIPTION = f"{AUTO_MARKER} agforge request records: {FALLBACK_PROJECT}"

_LABEL_CACHE: dict[tuple[str, str], str] = {}

__all__ = [
    "AUTO_MARKER",
    "FORGE_LABEL",
    "PlaneError",
    "ensure_label",
    "external_id",
    "register_plan",
    "resolve_project",
]


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


def register_plan(channel: str, topic: str, plan: Path) -> str:
    """Register one generator `plan.md` as this topic's Plane Work.

    Returns the report line(s) for the topic. Updating through the same
    external key is what keeps one topic to one Work.
    """
    title, description = split_document(plan.read_text(encoding="utf-8"))
    config = load_plane_config()
    project, note = resolve_project(config, channel)
    project_id = str(project["id"])
    key = external_id(channel, topic)
    if existing := find_issue_by_external(config, project_id, EXTERNAL_SOURCE, key):
        update_issue(
            config, project_id, str(existing["id"]),
            {"name": title.strip(), "description_html": description_html(description)},
        )
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
            # The label is what `runcreate-` work selection picks up — and
            # what autolab's `next_work` (which wants `AUTO`) passes over.
            labels=[ensure_label(config, project_id)],
        )
        line = f'created {issue_label(project, issue)} "{title}"'
    line += f" in {project.get('name', '?')}"
    return f"{note}\n{line}" if note else line
