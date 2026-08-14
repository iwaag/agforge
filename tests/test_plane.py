"""Plane registration: routing, the duplicate guard, and what is NOT written.

No Plane instance is involved — `_request_json` is the seam, and these pin
the request bodies and the routing decisions made around them. The one thing
worth stating loudly: an agforge Work carries no `AUTO` label and its project
no `[AUTO]` marker, because either would hand the issue to agautolab's
`next_work` to execute.
"""

import urllib.parse

import pytest

from agforge import plane

CONFIG = plane.PlaneConfig("http://plane.invalid", "key", "ws")
FREEFORGE = {"id": "p-free", "name": "FreeForge", "identifier": "FF"}
DEMO = {"id": "p-demo", "name": "Demo Project", "identifier": "DP"}
STATES = [
    {"id": "s-backlog", "name": "Backlog", "group": "backlog"},
    {"id": "s-ready", "name": "Ready", "group": "unstarted"},
    {"id": "s-done", "name": "Done", "group": "completed"},
]


class Plane:
    """A Plane whose whole surface is the four calls this module makes."""

    def __init__(self, projects=(FREEFORGE,), issues=None):
        self.projects = list(projects)
        self.issues = dict(issues or {})
        self.calls = []
        self.next_sequence = 7

    def __call__(self, method, url, *, headers, body=None, timeout=30):
        self.calls.append((method, url, body))
        if method == "GET" and "/projects/?" in url:
            return 200, {"results": self.projects}
        if method == "POST" and url.endswith("/projects/"):
            created = {
                "id": f"p-{len(self.projects)}",
                "name": body["name"],
                "identifier": body["identifier"],
                "description": body.get("description", ""),
            }
            self.projects.append(created)
            return 201, created
        if method == "GET" and "/states/" in url:
            return 200, {"results": STATES}
        if method == "GET" and "/issues/?" in url:
            external = urllib.parse.unquote(url.split("external_id=", 1)[1].split("&", 1)[0])
            found = self.issues.get(external)
            return (200, found) if found else (404, {"detail": "not found"})
        if method == "POST" and url.endswith("/issues/"):
            issue = {"id": f"i-{len(self.issues)}", "sequence_id": self.next_sequence, **body}
            self.issues[body["external_id"]] = issue
            return 201, issue
        if method == "PATCH" and "/issues/" in url:
            return 200, {"id": "patched", **(body or {})}
        raise AssertionError(f"unexpected call: {method} {url}")

    def created_issues(self):
        return [body for method, url, body in self.calls
                if method == "POST" and url.endswith("/issues/")]

    def created_projects(self):
        return [body for method, url, body in self.calls
                if method == "POST" and url.endswith("/projects/")]


def wire(monkeypatch, fake, config=CONFIG):
    monkeypatch.setattr(plane, "_request_json", fake)
    monkeypatch.setattr(plane, "load_plane_config", lambda path=None: config)


def plan_file(tmp_path, text="# Draw the bird\n\nOne 64x64 PNG.\n"):
    path = tmp_path / "plan.md"
    path.write_text(text)
    return path


# --- what must never be written -------------------------------------------


def test_a_registered_work_carries_no_labels(monkeypatch, tmp_path):
    """`labels` is the one place autolab's AUTO label would attach, and an
    AUTO-labelled issue is one `next_work` will pick up and execute."""
    fake = Plane()
    wire(monkeypatch, fake)
    plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    body = fake.created_issues()[0]
    assert "labels" not in body
    assert (body["external_source"], body["external_id"]) == ("agforge", "FreeForge/create-x")


def test_a_created_project_carries_no_auto_marker(monkeypatch, tmp_path):
    fake = Plane(projects=[])
    wire(monkeypatch, fake)
    plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    created = fake.created_projects()[0]
    assert created["name"] == "FreeForge"
    assert "[AUTO]" not in created["description"].upper()


# --- routing ---------------------------------------------------------------


def test_a_project_channel_routes_to_its_own_project(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE, DEMO])
    wire(monkeypatch, fake)
    line = plane.register_plan("pj-demo-project", "create-x", plan_file(tmp_path))
    assert "in Demo Project" in line
    assert "FreeForge instead" not in line
    assert fake.created_issues()[0]["external_id"] == "pj-demo-project/create-x"


def test_a_missing_project_falls_back_to_freeforge_and_says_so(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE])
    wire(monkeypatch, fake)
    line = plane.register_plan("pj-absent", "create-x", plan_file(tmp_path))
    # A fallback is a routing fact reported on the topic, not a failure.
    assert "no Plane project named 'absent'" in line
    assert "registering in FreeForge instead" in line
    assert "in FreeForge" in line.splitlines()[-1]


def test_a_non_project_channel_routes_to_freeforge(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE, DEMO])
    wire(monkeypatch, fake)
    line = plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    assert line == 'created FF-7 "Draw the bird" in FreeForge'


def test_freeforge_is_created_on_first_use(monkeypatch, tmp_path):
    fake = Plane(projects=[])
    wire(monkeypatch, fake)
    plane.register_plan("random-channel", "create-x", plan_file(tmp_path))
    assert [row["name"] for row in fake.created_projects()] == ["FreeForge"]


# --- the duplicate guard ---------------------------------------------------


def test_serving_the_same_topic_twice_updates_one_work(monkeypatch, tmp_path):
    """The external key is the guard; N climbing must not fork the Work."""
    fake = Plane()
    wire(monkeypatch, fake)
    first = plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    second = plane.register_plan(
        "FreeForge", "create-x", plan_file(tmp_path, "# Draw a bluer bird\n\nAgain.\n")
    )
    assert first.startswith("created ")
    assert second.startswith("updated ")
    assert len(fake.created_issues()) == 1
    patched = [body for method, _, body in fake.calls if method == "PATCH"]
    assert patched[0]["name"] == "Draw a bluer bird"


def test_an_unknown_external_key_answers_404_not_an_empty_list(monkeypatch, tmp_path):
    fake = Plane()
    wire(monkeypatch, fake)
    assert plane.find_issue_by_external(CONFIG, "p-free", "nothing/here") is None


# --- documents and states --------------------------------------------------


def test_plan_splits_into_a_title_and_a_description():
    assert plane.split_document("# Bird\n\nDraw it.\n") == ("Bird", "Draw it.")
    assert plane.split_document("Just a line\nand more\n") == ("Just a line", "and more")
    with pytest.raises(plane.PlaneError):
        plane.split_document("\n\n")


def test_starting_state_prefers_ready(monkeypatch):
    fake = Plane()
    wire(monkeypatch, fake)
    assert plane.starting_state_id(CONFIG, "p-free") == "s-ready"


def test_starting_state_falls_back_through_the_unstarted_group(monkeypatch):
    def only_backlog(method, url, *, headers, body=None, timeout=30):
        return 200, {"results": [{"id": "s-b", "name": "Backlog", "group": "backlog"}]}

    monkeypatch.setattr(plane, "_request_json", only_backlog)
    assert plane.starting_state_id(CONFIG, "p-free") == "s-b"


def test_credentials_are_read_from_the_shared_pj_agdev_file():
    """agforge's root has the same parent as agautolab's, so the one
    credentials file is reached by the same expression."""
    assert plane.PLANE_ENV.name == "plane-credentials.env"
    assert plane.PLANE_ENV.parent.name == ".local"


def test_missing_credentials_are_reported_not_swallowed(tmp_path):
    (tmp_path / "plane.env").write_text("PLANE_URL=http://x\n")
    with pytest.raises(plane.PlaneError) as caught:
        plane.load_plane_config(tmp_path / "plane.env")
    assert "PLANE_API_KEY" in str(caught.value)
