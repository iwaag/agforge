"""Which Work agforge's `assetrun-` flow executes next, and reporting back.

Ported locally from agautolab's `mission.py` (`eligible_works`, `next_work`,
`report_work`), the same rhythm as p1: local first, pyagag sharing is Step 6's
optional refactor. What differs from autolab is agforge's vocabulary — the
label is `FORGEAUTO`, the external source is `"agforge"` — and that a chosen
Work carries its origin: p1 already stores the requesting `<channel>/<topic>`
as the Work's `external_id`, which is where the result gets delivered back.

Selection is pure policy over Plane row dicts. Eligible means: carries the
`FORGEAUTO` label, sits in an `unstarted` state, and is not the parent of
another issue. agforge creates no sub-works today, but the parent check and
the serial tiebreak cost nothing and survive future sub-works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agag.plane import (
    add_comment,
    html_to_text,
    issue_label,
    labels_by_name,
    list_issues,
    list_projects,
    state_groups,
    state_id_for_group,
    update_issue,
)

from .plane import AUTO_MARKER, EXTERNAL_SOURCE, FORGE_LABEL, load_plane_config

SUB_WORK_SERIAL = re.compile(r"#(?P<number>\d+)\s*$")

__all__ = ["Work", "eligible_works", "next_work", "report_work", "sub_work_serial"]


@dataclass(frozen=True)
class Work:
    """One chosen Work, with everything the assetrun flow needs of it."""

    project_id: str
    issue_id: str
    name: str
    description: str
    external_source: str
    external_id: str

    def origin(self) -> tuple[str, str] | None:
        """`(channel, topic)` the request came from, or None.

        p1's `register_plan` keys every Work `<channel>/<topic>`; a hand-made
        or foreign-source Work has no origin to deliver to.
        """
        if self.external_source != EXTERNAL_SOURCE:
            return None
        channel, _, topic = self.external_id.partition("/")
        return (channel, topic) if channel and topic else None


def project_marked(row: dict) -> bool:
    """Whether a project's description opts it into automatic execution —
    the same `[AUTO]` marker autolab scans, so a `pj-*` project's Works are
    treated the same as FreeForge's once labelled."""
    return str(row.get("description") or "").strip().upper().startswith(AUTO_MARKER)


def sub_work_serial(external_id: str | None) -> int:
    """The `#<N>` tail of a sub-work external id.

    Works and hand-made issues have no serial; they sort last within the same
    creation timestamp, which is all this number is used for.
    """
    match = SUB_WORK_SERIAL.search(str(external_id or ""))
    return int(match.group("number")) if match else 1 << 30


def eligible_works(issues: list[dict], groups: dict[str, str], label_id: str) -> list[dict]:
    """Issues that may be executed automatically, in execution order.

    Eligible means: carries the `FORGEAUTO` label, sits in an `unstarted`
    state, and has no sub-work at all — a parent is executed through its
    children. Order is creation time first, then the serial tiebreak.
    """
    parents = {str(row.get("parent") or "") for row in issues} - {""}
    matches = [
        row
        for row in issues
        if label_id in {str(value) for value in (row.get("labels") or [])}
        and groups.get(str(row.get("state") or "")) == "unstarted"
        and str(row.get("id")) not in parents
    ]
    matches.sort(key=lambda row: (str(row.get("created_at") or ""),
                                  sub_work_serial(row.get("external_id")),
                                  str(row.get("id"))))
    return matches


def next_work() -> Work | None:
    """The next Work to execute, across every `[AUTO]`-marked project.

    A project without a `FORGEAUTO` label has no agforge work by definition;
    autolab's projects carry `AUTO` instead and are skipped here the same way
    FreeForge is skipped there. `None` when nothing is eligible.
    """
    config = load_plane_config()
    candidates: list[tuple[tuple, str, dict]] = []
    for row in list_projects(config):
        if not project_marked(row) or not row.get("id"):
            continue
        project_id = str(row["id"])
        label_id = labels_by_name(config, project_id).get(FORGE_LABEL.lower())
        if not label_id:
            continue
        issues = list_issues(config, project_id)
        groups = state_groups(config, project_id)
        for issue in eligible_works(issues, groups, label_id):
            candidates.append(
                (
                    (str(issue.get("created_at") or ""),
                     sub_work_serial(issue.get("external_id")),
                     str(issue.get("id"))),
                    project_id,
                    issue,
                )
            )
    if not candidates:
        return None
    _, project_id, issue = min(candidates, key=lambda item: item[0])
    return Work(
        project_id,
        str(issue["id"]),
        str(issue.get("name", "")),
        html_to_text(issue.get("description_html")),
        str(issue.get("external_source") or ""),
        str(issue.get("external_id") or ""),
    )


def report_work(
    project_id: str, issue_id: str, report: str | None, success: bool
) -> tuple[str, bool, bool]:
    """Write one executed Work's outcome back to Plane.

    Comments `report` on the issue when there is one, and moves the issue to
    the project's `completed` state when the run reported success — without
    which the same Work is re-selected on every trigger. Returns
    `(work label, commented, completed)` for the chat outcome line.
    """
    config = load_plane_config()
    project_row = next(
        (row for row in list_projects(config) if str(row.get("id")) == project_id), {}
    )
    issue = next(
        (row for row in list_issues(config, project_id) if str(row.get("id")) == issue_id),
        {"id": issue_id},
    )
    label = issue_label(project_row, issue)
    commented = bool(report and report.strip())
    if commented:
        add_comment(config, project_id, issue_id, report.strip())
    if success:
        update_issue(
            config, project_id, issue_id,
            {"state": state_id_for_group(config, project_id, "completed")},
        )
    return label, commented, success
