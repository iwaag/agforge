"""agforge's Plane policy: routing, and what is deliberately not written.

The client itself (`agag.plane`) is tested in pyagag. What is pinned here is
only agforge's own decisions — which project a channel routes to, and the two
absences that keep an agforge Work out of agautolab's `next_work`.
"""

import urllib.parse

import pytest
from agag import plane as shared

from agforge import plane

CONFIG = shared.PlaneConfig("http://plane.invalid", "key", "ws")
FREEFORGE = {"id": "p-free", "name": "FreeForge", "identifier": "FF"}
DEMO = {"id": "p-demo", "name": "Demo Project", "identifier": "DP"}
STATES = [
    {"id": "s-backlog", "name": "Backlog", "group": "backlog"},
    {"id": "s-ready", "name": "Ready", "group": "unstarted"},
]


class Plane:
    """A Plane whose whole surface is the calls this policy makes."""

    def __init__(self, projects=(FREEFORGE,)):
        self.projects = list(projects)
        self.issues = {}
        self.calls = []

    def __call__(self, method, url, *, headers, body=None, timeout=30):
        self.calls.append((method, url, body))
        if method == "GET" and "/projects/?" in url:
            return 200, {"results": self.projects}
        if method == "POST" and url.endswith("/projects/"):
            created = {"id": f"p-{len(self.projects) + 1}", **body}
            self.projects.append(created)
            return 201, created
        if method == "GET" and "/states/" in url:
            return 200, {"results": STATES}
        if method == "GET" and "external_id=" in url:
            key = urllib.parse.unquote(url.split("external_id=", 1)[1].split("&", 1)[0])
            found = self.issues.get(key)
            return (200, found) if found else (404, {"detail": "not found"})
        if method == "POST" and url.endswith("/issues/"):
            issue = {"id": f"i-{len(self.issues) + 1}", "sequence_id": 7, **body}
            self.issues[body["external_id"]] = issue
            return 201, issue
        if method == "PATCH":
            return 200, {"id": "patched", **(body or {})}
        raise AssertionError(f"unexpected call: {method} {url}")

    def bodies(self, method, suffix):
        return [b for m, url, b in self.calls if m == method and url.endswith(suffix)]


def wire(monkeypatch, fake):
    monkeypatch.setattr(shared, "_request_json", fake)
    monkeypatch.setattr(plane, "load_plane_config", lambda path=None: CONFIG)


def plan_file(tmp_path, text="# Draw the bird\n\nOne 64x64 PNG.\n"):
    path = tmp_path / "plan.md"
    path.write_text(text)
    return path


# --- what must never be written -------------------------------------------


def test_a_registered_work_carries_no_labels(monkeypatch, tmp_path):
    """`labels` is the one place autolab's AUTO label would attach, and an
    AUTO-labelled issue is one `next_work` picks up and executes."""
    fake = Plane()
    wire(monkeypatch, fake)
    plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    body = fake.bodies("POST", "/issues/")[0]
    assert "labels" not in body
    assert (body["external_source"], body["external_id"]) == ("agforge", "FreeForge/create-x")


def test_a_created_project_carries_no_auto_marker(monkeypatch, tmp_path):
    fake = Plane(projects=[])
    wire(monkeypatch, fake)
    plane.register_plan("FreeForge", "create-x", plan_file(tmp_path))
    created = fake.bodies("POST", "/projects/")[0]
    assert created["name"] == "FreeForge"
    assert "[AUTO]" not in created["description"].upper()


# --- routing ---------------------------------------------------------------


def test_a_project_channel_routes_to_its_own_project(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE, DEMO])
    wire(monkeypatch, fake)
    line = plane.register_plan("pj-demo-project", "create-x", plan_file(tmp_path))
    assert "in Demo Project" in line
    assert "instead" not in line
    assert fake.bodies("POST", "/issues/")[0]["external_id"] == "pj-demo-project/create-x"


def test_a_missing_project_falls_back_to_freeforge_and_says_so(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE])
    wire(monkeypatch, fake)
    line = plane.register_plan("pj-absent", "create-x", plan_file(tmp_path))
    # A fallback is a routing fact reported on the topic, not a failure.
    assert "no Plane project named 'absent'" in line
    assert "registering in FreeForge instead" in line
    assert "in FreeForge" in line.splitlines()[-1]


def test_a_malformed_project_channel_falls_back_too(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE])
    wire(monkeypatch, fake)
    line = plane.register_plan("pj-Bad_Name", "create-x", plan_file(tmp_path))
    assert "does not carry a valid project name" in line


def test_a_non_project_channel_routes_to_freeforge(monkeypatch, tmp_path):
    fake = Plane(projects=[FREEFORGE, DEMO])
    wire(monkeypatch, fake)
    assert plane.register_plan("FreeForge", "create-x", plan_file(tmp_path)) == (
        'created FF-7 "Draw the bird" in FreeForge'
    )


def test_freeforge_is_created_on_first_use(monkeypatch, tmp_path):
    fake = Plane(projects=[])
    wire(monkeypatch, fake)
    plane.register_plan("random-channel", "create-x", plan_file(tmp_path))
    assert [row["name"] for row in fake.bodies("POST", "/projects/")] == ["FreeForge"]


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
    assert len(fake.bodies("POST", "/issues/")) == 1
    assert fake.bodies("PATCH", "/")[0]["name"] == "Draw a bluer bird"


def test_the_external_key_is_the_channel_and_topic():
    assert plane.external_id("pj-x", "create-y") == "pj-x/create-y"


def test_credentials_come_from_the_shared_pj_agdev_file():
    """agforge's root has the same parent as agautolab's, so one file serves
    both agents."""
    assert plane.PLANE_ENV.name == "plane-credentials.env"
    assert plane.PLANE_ENV.parent.name == ".local"


def test_a_missing_credentials_file_is_reported(tmp_path):
    with pytest.raises(shared.PlaneError):
        plane.load_plane_config(tmp_path / "absent.env")
