"""The runcreate- topic: a button, not a conversation.

Pinned here: the ack-then-always-answer discipline (the final post is both
the report and the sweep's off-switch), the "no work" path, the failed-step
naming, the workspace shape (persistent, overwrite-in-place, no dirty
check), and `dispatch`'s routing. Nothing asserts what an agent said.
"""

import pytest

from agforge import runcreate_topic, zulip_listener
from agforge.works import Work

CHANNEL = "FreeForge"
TOPIC = "runcreate-20260815"
WORK = Work("p-free", "issue-1", "Draw the bird", "One 64x64 PNG.",
            "agforge", "FreeForge/create-x")


class Client:
    """handle_runcreate only writes; the chatlog is never read."""


def wire(monkeypatch, tmp_path, calls, *, chosen=WORK, answer="made it"):
    monkeypatch.setattr(runcreate_topic, "AGENTWS_ROOT", tmp_path / "agentws")
    monkeypatch.setattr(runcreate_topic, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        runcreate_topic,
        "topic_write",
        lambda topic, text, **kwargs: calls.append(("write", topic, text)) or "success",
    )
    monkeypatch.setattr(runcreate_topic, "next_work", lambda: chosen)

    def generator_run(workspace):
        calls.append(("generator", workspace))
        return answer

    monkeypatch.setattr(runcreate_topic, "run_generator", generator_run)


def ws(tmp_path):
    return tmp_path / "agentws" / WORK.issue_id / "generator"


# --- (a) no eligible work ---------------------------------------------------


def test_no_work_still_answers_the_topic(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, chosen=None)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert [call[0] for call in calls] == ["write", "write"]
    assert calls[0][1:] == (TOPIC, runcreate_topic.SWEEP_ACK)
    assert calls[1][1:] == (TOPIC, runcreate_topic.NO_WORK_REPLY)
    assert not (tmp_path / "agentws").exists()


# --- (b) the success path ---------------------------------------------------


def test_success_builds_the_workspace_runs_and_summarizes(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    workspace = ws(tmp_path)
    assert [call[0] for call in calls] == ["write", "generator", "write"]
    assert calls[1][1] == workspace
    assert (workspace / "plan.md").read_text() == "# Draw the bird\n\nOne 64x64 PNG.\n"
    assert "generate.sh" in (workspace / "tools.md").read_text()
    assert (workspace / "result").is_dir()
    assert (workspace / "intermediate").is_dir()
    summary = calls[-1][2]
    assert 'running "Draw the bird"' in summary
    assert "made it" in summary


def test_a_retrigger_overwrites_in_place_and_keeps_results(monkeypatch, tmp_path):
    """Persistent workspace, no dirty check: plan.md/tools.md are refreshed,
    result/ and intermediate/ keep whatever an earlier run left."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    workspace = ws(tmp_path)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    (workspace / "result" / "bird.png").write_text("old bytes")
    (workspace / "plan.md").write_text("stale")

    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    assert (workspace / "plan.md").read_text() == "# Draw the bird\n\nOne 64x64 PNG.\n"
    assert (workspace / "result" / "bird.png").read_text() == "old bytes"


# --- (c) an exception mid-way names its step --------------------------------


def test_a_generator_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(workspace):
        raise runcreate_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(runcreate_topic, "run_generator", explode)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert calls[-1][2].endswith("failed during generator run: claude_code timed out")


def test_a_selection_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode():
        raise RuntimeError("plane is down")

    monkeypatch.setattr(runcreate_topic, "next_work", explode)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert calls[-1][2] == "failed during choosing the work: plane is down"


# --- (d) dispatch routing ---------------------------------------------------


@pytest.mark.parametrize("topic,expected", [
    ("create-20260815-x", "create"),
    ("runcreate-20260815", "runcreate"),
])
def test_dispatch_routes_by_prefix(monkeypatch, topic, expected):
    routed = []
    monkeypatch.setattr(
        "agforge.create_topic.handle_topic",
        lambda client, channel, t: routed.append("create"),
    )
    monkeypatch.setattr(
        "agforge.runcreate_topic.handle_runcreate",
        lambda client, channel, t: routed.append("runcreate"),
    )
    zulip_listener.dispatch(Client(), CHANNEL, topic)
    assert routed == [expected]


def test_the_sweep_covers_both_prefixes():
    assert zulip_listener.SWEEP_PREFIXES == ("runcreate-", "create-")
