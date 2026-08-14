"""Where agforge's create topics land in Plane.

The client itself is `agag.plane`, shared with agautolab. What lives here is
only agforge's policy on top of it: which project a channel routes to, and
the fact that an agforge Work is **unlabelled**.

That last point is the whole reason this file has an opinion. agautolab's
`next_work` executes any issue carrying the `AUTO` label, in any project whose
description carries the `[AUTO]` marker. An agforge Work records a request; it
is not a job for a coding agent. Neither marker is written here.
"""

from __future__ import annotations

import re
from pathlib import Path

from agag.plane import (
    PlaneError,
    create_project,
    description_html,
    ensure_issue,
    find_issue_by_external,
    find_project,
    issue_label,
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

__all__ = ["PlaneError", "external_id", "register_plan", "resolve_project"]


def load_plane_config(path: Path | None = None):
    return load_shared_plane_config(path or PLANE_ENV)


def external_id(channel: str, topic: str) -> str:
    """One topic, one key — however far the generation number climbs."""
    return f"{channel}/{topic}"


def _fallback(config) -> dict:
    return find_project(config, FALLBACK_PROJECT) or create_project(
        config,
        FALLBACK_PROJECT,
        # No `[AUTO]` marker: that marker is what makes autolab's `next_work`
        # scan a project at all.
        f"agforge request records: {FALLBACK_PROJECT}",
    )


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
            # No `labels`: an AUTO-labelled issue is one `next_work` executes.
        )
        line = f'created {issue_label(project, issue)} "{title}"'
    line += f" in {project.get('name', '?')}"
    return f"{note}\n{line}" if note else line
