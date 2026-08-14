"""Work selection is pure policy over Plane row dicts — tested with fixture
rows and monkeypatched listing functions, no HTTP, the way autolab tests its
`mission.py` original.
"""

from agforge import works


# --- eligibility over fixture rows -----------------------------------------


def test_eligible_works_filters_label_state_and_parents():
    groups = {"todo": "unstarted", "doing": "started", "done": "completed"}
    issues = [
        {"id": "w", "labels": ["forge-id"], "state": "todo", "created_at": "1",
         "external_id": "c/t"},                                       # has children
        {"id": "s1", "parent": "w", "labels": ["forge-id"], "state": "todo",
         "created_at": "2", "external_id": "c/t@1#1"},                # match
        {"id": "s2", "parent": "w", "labels": ["forge-id"], "state": "doing",
         "created_at": "2", "external_id": "c/t@1#2"},                # started
        {"id": "s3", "parent": "w", "labels": [], "state": "todo",
         "created_at": "2", "external_id": "c/t@1#3"},                # no label
        {"id": "lone", "labels": ["forge-id"], "state": "todo", "created_at": "3",
         "external_id": None},                                        # match, no serial
    ]
    assert [row["id"] for row in works.eligible_works(issues, groups, "forge-id")] == [
        "s1", "lone"
    ]


def test_eligible_works_orders_by_creation_then_serial():
    groups = {"todo": "unstarted"}
    rows = [
        {"id": "b2", "labels": ["a"], "state": "todo", "created_at": "2026-01-02",
         "external_id": "c/t@1#2"},
        {"id": "b1", "labels": ["a"], "state": "todo", "created_at": "2026-01-02",
         "external_id": "c/t@1#1"},
        {"id": "plain", "labels": ["a"], "state": "todo", "created_at": "2026-01-02",
         "external_id": "c/t"},
        {"id": "old", "labels": ["a"], "state": "todo", "created_at": "2026-01-01",
         "external_id": "c/u@1#9"},
    ]
    assert [row["id"] for row in works.eligible_works(rows, groups, "a")] == [
        "old", "b1", "b2", "plain"
    ]


# --- the origin channel/topic ----------------------------------------------


def test_the_origin_is_read_off_the_external_id():
    work = works.Work("p", "i", "n", "d", "agforge", "FreeForge/create-x")
    assert work.origin() == ("FreeForge", "create-x")


def test_a_topic_containing_slashes_survives_the_split():
    work = works.Work("p", "i", "n", "d", "agforge", "pj-demo/create-a/b")
    assert work.origin() == ("pj-demo", "create-a/b")


def test_a_foreign_or_hand_made_work_has_no_origin():
    assert works.Work("p", "i", "n", "d", "agautolab", "c/t").origin() is None
    assert works.Work("p", "i", "n", "d", "", "").origin() is None
    assert works.Work("p", "i", "n", "d", "agforge", "no-slash").origin() is None


# --- next_work over a fake workspace ---------------------------------------


def wire(monkeypatch, projects, issues_by_project, labels_by_project=None):
    monkeypatch.setattr(works, "load_plane_config", lambda: object())
    monkeypatch.setattr(works, "list_projects", lambda config: projects)
    monkeypatch.setattr(
        works, "labels_by_name",
        lambda config, pid: (labels_by_project or {}).get(pid, {"forgeauto": f"{pid}-forge"}),
    )
    monkeypatch.setattr(works, "list_issues", lambda config, pid: issues_by_project[pid])
    monkeypatch.setattr(works, "state_groups", lambda config, pid: {"todo": "unstarted"})


def test_next_work_picks_the_oldest_across_marked_projects(monkeypatch):
    projects = [
        {"id": "p1", "name": "FreeForge", "description": "[AUTO] agforge request records"},
        {"id": "p2", "name": "Demo", "description": "[AUTO] autolab project: demo"},
        {"id": "p3", "name": "ProjectA", "description": "hand made"},
    ]
    issues = {
        "p1": [
            {"id": "i1", "name": "Later", "labels": ["p1-forge"], "state": "todo",
             "created_at": "2026-01-05", "external_id": "FreeForge/create-later",
             "external_source": "agforge",
             "description_html": "<p>second</p>"},
        ],
        "p2": [
            {"id": "i2", "name": "Older", "labels": ["p2-forge"], "state": "todo",
             "created_at": "2026-01-01", "external_id": "pj-demo/create-old",
             "external_source": "agforge",
             "description_html": "<p>first</p>"},
        ],
    }
    wire(monkeypatch, projects, issues)
    chosen = works.next_work()
    assert (chosen.project_id, chosen.issue_id, chosen.name) == ("p2", "i2", "Older")
    assert chosen.description == "first"
    assert chosen.origin() == ("pj-demo", "create-old")


def test_a_project_without_the_label_is_skipped(monkeypatch):
    """autolab's projects carry AUTO, not FORGEAUTO — the label filter is what
    keeps the two agents off each other's work."""
    projects = [
        {"id": "p1", "name": "Demo", "description": "[AUTO] autolab project: demo"},
    ]
    issues = {
        "p1": [
            {"id": "i1", "name": "Autolab's", "labels": ["p1-auto"], "state": "todo",
             "created_at": "2026-01-01"},
        ],
    }
    wire(monkeypatch, projects, issues, labels_by_project={"p1": {"auto": "p1-auto"}})
    assert works.next_work() is None


def test_an_unmarked_project_is_never_scanned(monkeypatch):
    projects = [{"id": "p1", "name": "ProjectA", "description": "hand made"}]
    scanned = []

    def listing(config, pid):
        scanned.append(pid)
        return []

    wire(monkeypatch, projects, {})
    monkeypatch.setattr(works, "list_issues", listing)
    assert works.next_work() is None
    assert scanned == []


def test_nothing_eligible_means_none(monkeypatch):
    projects = [{"id": "p1", "name": "FreeForge", "description": "[AUTO] records"}]
    wire(monkeypatch, projects, {"p1": []})
    assert works.next_work() is None


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
