"""What an `assetrun-` topic runs, and how its outcome is written back.

Selection is gone: since `agent_standardize` p8 the topic names its Work, so
what is pinned here is the lookup (`work_by_id`), the origin key, and
`report_work`. Fixture rows and monkeypatched listing functions, no HTTP, the
way autolab tests its `mission.py` original.
"""

from agforge import works


# --- the origin channel/topic ----------------------------------------------


def test_the_origin_is_read_off_the_external_id():
    work = works.Work("p", "i", "n", "d", "agforge", "FreeForge/assetplan-x")
    assert work.origin() == ("FreeForge", "assetplan-x")


def test_a_topic_containing_slashes_survives_the_split():
    work = works.Work("p", "i", "n", "d", "agforge", "pj-demo/assetplan-a/b")
    assert work.origin() == ("pj-demo", "assetplan-a/b")


def test_a_foreign_or_hand_made_work_has_no_origin():
    assert works.Work("p", "i", "n", "d", "agautolab", "c/t").origin() is None
    assert works.Work("p", "i", "n", "d", "", "").origin() is None
    assert works.Work("p", "i", "n", "d", "agforge", "no-slash").origin() is None


# --- the lookup over a fake workspace --------------------------------------


def wire(monkeypatch, issues_by_project):
    monkeypatch.setattr(works, "load_plane_config", lambda: object())
    monkeypatch.setattr(
        works, "list_issues", lambda config, pid: issues_by_project.get(pid, [])
    )


def test_work_by_id_returns_the_work_the_topic_names(monkeypatch):
    wire(monkeypatch, {"p1": [
        {"id": "i0", "name": "Another", "description_html": "<p>no</p>"},
        {"id": "i1", "name": "The one", "external_id": "FreeForge/assetplan-x",
         "external_source": "agforge", "description_html": "<p>yes</p>"},
    ]})
    work = works.work_by_id("p1", "i1")
    assert (work.project_id, work.issue_id, work.name) == ("p1", "i1", "The one")
    assert work.description == "yes"
    assert work.origin() == ("FreeForge", "assetplan-x")


def test_a_finished_work_is_still_returned(monkeypatch):
    """The topic decided, not the state: a re-trigger is a legitimate retry,
    and there is no eligibility left to consult."""
    wire(monkeypatch, {"p1": [
        {"id": "i1", "name": "Done already", "state": "completed", "labels": [],
         "description_html": "<p>x</p>"},
    ]})
    assert works.work_by_id("p1", "i1").name == "Done already"


def test_a_work_that_is_gone_is_none(monkeypatch):
    wire(monkeypatch, {"p1": []})
    assert works.work_by_id("p1", "i1") is None


# --- reporting back ---------------------------------------------------------


def test_report_work_comments_and_completes(monkeypatch):
    written = []
    monkeypatch.setattr(works, "load_plane_config", lambda: object())
    monkeypatch.setattr(works, "list_projects", lambda config: [
        {"id": "p1", "identifier": "FF"}
    ])
    monkeypatch.setattr(works, "list_issues", lambda config, pid: [
        {"id": "i1", "sequence_id": 7}
    ])
    monkeypatch.setattr(
        works, "add_comment",
        lambda config, pid, iid, text: written.append(("comment", iid, text)),
    )
    monkeypatch.setattr(
        works, "state_id_for_group", lambda config, pid, group: f"s-{group}"
    )
    monkeypatch.setattr(
        works, "update_issue",
        lambda config, pid, iid, body: written.append(("update", iid, body)),
    )

    label, commented, completed = works.report_work("p1", "i1", "made it", True)
    assert (label, commented, completed) == ("FF-7", True, True)
    assert written == [
        ("comment", "i1", "made it"),
        ("update", "i1", {"state": "s-completed"}),
    ]

    written.clear()
    label, commented, completed = works.report_work("p1", "i1", "  ", False)
    assert (label, commented, completed) == ("FF-7", False, False)
    assert written == []
