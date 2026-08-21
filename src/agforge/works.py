"""Which Work an `assetrun-` topic runs, and reporting its outcome back.

Ported locally from agautolab's `mission.py` (`report_work`), the same rhythm
as p1. What differs from autolab is agforge's vocabulary — the external
source is `"agforge"` — and that a Work carries its origin: p1 stores the
requesting `<channel>/<topic>` as the Work's `external_id`, which is where a
pre-p8 Work's result gets delivered back.

**There is no selection here any more.** Until `agent_standardize` p8 this
module chose a Work — `next_work`, "the oldest eligible one across every
`[AUTO]` project" — because an `assetrun-` topic was a bare button that said
nothing about itself. Since p8 the topic is opened by the plan it belongs to
and carries the Work's id in a selfnote, so the answer is looked up
(`work_by_id`) rather than guessed at, and the requester is no longer
responsible for a queue they cannot see. The `FORGEAUTO` label still marks
agforge's Works apart from autolab's; nothing reads it to decide what to run.
"""

from __future__ import annotations

from dataclasses import dataclass

from agag.plane import (
    add_comment,
    html_to_text,
    issue_label,
    list_issues,
    list_projects,
    state_id_for_group,
    update_issue,
)

from .plane import EXTERNAL_SOURCE, load_plane_config

__all__ = ["Work", "report_work", "work_by_id"]


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


def work_by_id(project_id: str, issue_id: str) -> Work | None:
    """The Work an `assetrun-` topic names, or None if it is gone.

    Since `agent_standardize` p8 this is how a trigger finds its Work: the
    topic was opened for one Work and says so, and the alternative —
    `next_work`'s "whichever eligible one sorts first" — was a guess that
    made the requester responsible for the queue. Neither the label nor the
    state is consulted here: the topic already decided, and a Work that has
    been run once is re-runnable from its own topic.
    """
    config = load_plane_config()
    for issue in list_issues(config, project_id):
        if str(issue.get("id")) != issue_id:
            continue
        return Work(
            project_id,
            str(issue["id"]),
            str(issue.get("name", "")),
            html_to_text(issue.get("description_html")),
            str(issue.get("external_source") or ""),
            str(issue.get("external_id") or ""),
        )
    return None


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
