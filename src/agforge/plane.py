"""Plane mirror of one create topic: its Work, and where that Work lives.

One direction only. The generator's `plan.md` becomes a Plane issue keyed on
Plane's own `(external_source, external_id)` pair — `agforge` and
`<channel>/<topic>` — so re-serving a topic updates the same Work however far
the generation number `N` climbs, and a wiped `.local/` changes nothing.

Deliberately **unlabelled**. agautolab's `next_work` executes any issue
carrying the `AUTO` label, and picks its projects by an `[AUTO]` marker in the
project description; a Work agforge registers is a record of a request, not a
queued job for a coding agent. Neither marker is written here.

Local to agforge for now. The plan shares this out into `agag` in step 5;
keeping it here is what keeps a pyagag push → `uv lock` round trip out of the
implementation loop.
"""

from __future__ import annotations

import html
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .role_run import AGFORGE_ROOT

# agforge's root has the same parent as agautolab's, so the credentials file
# both agents share is reached by the same expression.
PLANE_ENV = AGFORGE_ROOT.parent / ".local" / "plane-credentials.env"

EXTERNAL_SOURCE = "agforge"
PROJECT_CHANNEL_PREFIX = "pj-"
FALLBACK_PROJECT = "FreeForge"
TITLE_LIMIT = 255
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")


class PlaneError(RuntimeError):
    """One Plane operation could not complete."""


@dataclass(frozen=True)
class PlaneConfig:
    url: str
    api_key: str
    workspace: str


# --- credentials -----------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PlaneError(f"cannot read configuration {path}: {error}") from error
    values: dict[str, str] = {}
    for line in lines:
        tokens = shlex.split(line, comments=True)
        if len(tokens) == 1 and "=" in tokens[0]:
            key, value = tokens[0].split("=", 1)
            values[key] = value
    return values


def load_plane_config(path: Path | None = None) -> PlaneConfig:
    values = read_env(path or PLANE_ENV)
    required = ("PLANE_URL", "PLANE_API_KEY", "PLANE_WORKSPACE_SLUG")
    if missing := [key for key in required if not values.get(key)]:
        raise PlaneError(f"{path or PLANE_ENV} is missing {', '.join(missing)}")
    return PlaneConfig(
        os.environ.get("PLANE_URL", values["PLANE_URL"]).rstrip("/"),
        os.environ.get("PLANE_API_KEY", values["PLANE_API_KEY"]),
        os.environ.get("PLANE_WORKSPACE_SLUG", values["PLANE_WORKSPACE_SLUG"]),
    )


# --- HTTP ------------------------------------------------------------------


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict | None = None,
    timeout: float = 30,
) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw.decode("utf-8", "replace")[:200]}
        return error.code, payload
    except (OSError, TimeoutError) as error:
        raise PlaneError(f"{method} {url} failed: {error}") from error


def _rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [row for row in payload["results"] if isinstance(row, dict)]
    raise PlaneError("API response did not contain a result list")


def _headers(config: PlaneConfig) -> dict[str, str]:
    return {"X-API-Key": config.api_key, "Content-Type": "application/json"}


def _workspace_base(config: PlaneConfig) -> str:
    return (
        f"{config.url}/api/v1/workspaces/"
        f"{urllib.parse.quote(config.workspace, safe='')}"
    )


def _project_base(config: PlaneConfig, project_id: str) -> str:
    return f"{_workspace_base(config)}/projects/{urllib.parse.quote(project_id, safe='')}"


# --- documents -------------------------------------------------------------


def _normalized_name(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.lower()))


def split_document(text: str) -> tuple[str, str]:
    """Split one Markdown file into a Plane issue title and description.

    Title is the first heading line; without one, the first non-empty line.
    Everything else, in file order, is the description.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if match := HEADING.match(line):
            title = match.group("title")
            break
        if line.strip():
            title = line.strip()
            break
    else:
        raise PlaneError("the file is empty")
    description = "\n".join(lines[:index] + lines[index + 1 :]).strip()
    return title[:TITLE_LIMIT], description


def description_html(description: str) -> str:
    return f"<p>{html.escape(description).replace(chr(10), '<br>')}</p>"


# --- projects --------------------------------------------------------------


def list_projects(config: PlaneConfig) -> list[dict]:
    status, payload = _request_json(
        "GET", f"{_workspace_base(config)}/projects/?per_page=100", headers=_headers(config)
    )
    if status != 200:
        raise PlaneError(f"Plane project list returned HTTP {status}: {payload!r}")
    return _rows(payload)


def find_project(config: PlaneConfig, name: str) -> dict | None:
    wanted = _normalized_name(name)
    return next(
        (
            row
            for row in list_projects(config)
            if _normalized_name(str(row.get("name", ""))) == wanted and row.get("id")
        ),
        None,
    )


def plane_identifier(name: str) -> str:
    parts = [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]
    return "".join(part if part.isdigit() else part[0] for part in parts).upper()[:12]


def create_project(config: PlaneConfig, name: str) -> dict:
    """Create one project. Its description carries **no** `[AUTO]` marker —
    that marker is what makes autolab's `next_work` scan a project."""
    used = {str(row.get("identifier", "")).upper() for row in list_projects(config)}
    base_identifier = plane_identifier(name) or "FF"
    for suffix in range(1, 101):
        tail = "" if suffix == 1 else str(suffix)
        identifier = f"{base_identifier[:12 - len(tail)]}{tail}"
        if identifier in used:
            continue
        status, payload = _request_json(
            "POST",
            f"{_workspace_base(config)}/projects/",
            headers=_headers(config),
            body={
                "name": name,
                "identifier": identifier,
                "description": f"agforge request records: {name}",
            },
            timeout=60,
        )
        if status in {200, 201} and isinstance(payload, dict) and payload.get("id"):
            return payload
        if status in {409, 422}:
            if existing := find_project(config, name):
                return existing
            used.update(str(row.get("identifier", "")).upper() for row in list_projects(config))
            continue
        raise PlaneError(f"Plane project create returned HTTP {status}: {payload!r}")
    raise PlaneError("Plane identifier collision retries exhausted")


def resolve_project(config: PlaneConfig, channel: str) -> tuple[dict, str | None]:
    """Where a channel's Works live: `(project, note)`.

    `pj-<name>` routes to the Plane project of that name. Anything else — and
    a `pj-` channel whose project does not exist — routes to `FreeForge`,
    which is created on first use. A fallback is reported in `note`, not
    raised: an unregistered project name is a routing fact, not a failure.
    """
    if channel.startswith(PROJECT_CHANNEL_PREFIX):
        name = channel.removeprefix(PROJECT_CHANNEL_PREFIX)
        if PROJECT_NAME.fullmatch(name):
            if project := find_project(config, name):
                return project, None
            note = (
                f"no Plane project named {name!r}; "
                f"registering in {FALLBACK_PROJECT} instead"
            )
        else:
            note = (
                f"{channel!r} does not carry a valid project name; "
                f"registering in {FALLBACK_PROJECT} instead"
            )
        return (find_project(config, FALLBACK_PROJECT) or create_project(
            config, FALLBACK_PROJECT
        )), note
    project = find_project(config, FALLBACK_PROJECT) or create_project(
        config, FALLBACK_PROJECT
    )
    return project, None


# --- issues ----------------------------------------------------------------


def starting_state_id(config: PlaneConfig, project_id: str) -> str:
    """The project's actionable initial state, from its live vocabulary."""
    status, payload = _request_json(
        "GET", f"{_project_base(config, project_id)}/states/",
        headers=_headers(config), timeout=60,
    )
    if status != 200:
        raise PlaneError(f"Plane state list returned HTTP {status}: {payload!r}")
    rows = _rows(payload)
    by_name = {str(row.get("name", "")).lower(): row for row in rows}
    state = by_name.get("ready") or by_name.get("todo")
    if state is None:
        state = next((row for row in rows if row.get("group") == "unstarted"), None)
    if state is None:
        state = by_name.get("backlog")
    if not state or not state.get("id"):
        raise PlaneError("Plane project has no usable starting state")
    return str(state["id"])


def find_issue_by_external(
    config: PlaneConfig, project_id: str, external_id: str
) -> dict | None:
    """Look one issue up by its `(external_source, external_id)` pair.

    Plane answers with the issue object itself, and **404** when the pair is
    unknown — not an empty list. This is the duplicate guard; no local marker
    file is involved.
    """
    query = urllib.parse.urlencode(
        {"external_id": external_id, "external_source": EXTERNAL_SOURCE}
    )
    status, payload = _request_json(
        "GET", f"{_project_base(config, project_id)}/issues/?{query}",
        headers=_headers(config),
    )
    if status == 404:
        return None
    if status != 200:
        raise PlaneError(f"Plane issue lookup returned HTTP {status}: {payload!r}")
    if isinstance(payload, dict) and payload.get("id"):
        return payload
    rows = _rows(payload) if isinstance(payload, (list, dict)) else []
    return rows[0] if rows else None


def update_issue(config: PlaneConfig, project_id: str, issue_id: str, body: dict) -> dict:
    status, payload = _request_json(
        "PATCH",
        f"{_project_base(config, project_id)}/issues/{urllib.parse.quote(issue_id, safe='')}/",
        headers=_headers(config),
        body=body,
        timeout=60,
    )
    if status != 200 or not isinstance(payload, dict):
        raise PlaneError(f"Plane issue update returned HTTP {status}: {payload!r}")
    return payload


def ensure_issue(
    config: PlaneConfig,
    project_id: str,
    *,
    name: str,
    description: str,
    state: str,
    external_id: str,
) -> tuple[dict, bool]:
    """Return `(issue, created)` for one external key, creating at most one.

    No `labels` are attached: that is the one place autolab's `AUTO` label
    would get onto an agforge Work and make it eligible for `next_work`.
    """
    if not name.strip():
        raise PlaneError("issue title must not be empty")
    if existing := find_issue_by_external(config, project_id, external_id):
        return existing, False
    status, payload = _request_json(
        "POST",
        f"{_project_base(config, project_id)}/issues/",
        headers=_headers(config),
        body={
            "name": name.strip(),
            "description_html": description_html(description),
            "state": state,
            "external_source": EXTERNAL_SOURCE,
            "external_id": external_id,
        },
        timeout=60,
    )
    if status in {200, 201} and isinstance(payload, dict):
        return payload, True
    if status == 409:
        if existing := find_issue_by_external(config, project_id, external_id):
            return existing, False
        if isinstance(payload, dict) and payload.get("id"):
            return {"id": payload["id"]}, False
    raise PlaneError(f"Plane issue create returned HTTP {status}: {payload!r}")


def issue_label(project: dict, issue: dict) -> str:
    identifier = str(project.get("identifier", "")).strip()
    sequence = issue.get("sequence_id")
    if identifier and sequence is not None:
        return f"{identifier}-{sequence}"
    return str(issue.get("id", "?"))


# --- the one thing the listener calls --------------------------------------


def register_plan(channel: str, topic: str, plan: Path) -> str:
    """Register one generator `plan.md` as this topic's Plane Work.

    Returns the report line(s) for the topic. Updating through the same
    external key is what keeps one topic to one Work, however often it is
    re-served.
    """
    title, description = split_document(plan.read_text(encoding="utf-8"))
    config = load_plane_config()
    project, note = resolve_project(config, channel)
    project_id = str(project["id"])
    external_id = f"{channel}/{topic}"
    existing = find_issue_by_external(config, project_id, external_id)
    if existing:
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
            external_id=external_id,
        )
        line = f'created {issue_label(project, issue)} "{title}"'
    line += f" in {project.get('name', '?')}"
    return f"{note}\n{line}" if note else line
