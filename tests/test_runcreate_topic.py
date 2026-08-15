"""The runcreate- topic: a button, not a conversation.

Pinned here: the ack-then-always-answer discipline (the final post is both
the report and the sweep's off-switch), the "no work" path, the failed-step
naming, the workspace shape (persistent, overwrite-in-place, no dirty
check), and `dispatch`'s routing. Nothing asserts what an agent said.
"""

import pytest

from agforge import runcreate_topic, toolsets, zulip_listener
from agforge.works import Work

CHANNEL = "FreeForge"
TOPIC = "runcreate-20260815"
WORK = Work("p-free", "issue-1", "Draw the bird",
            "One 64x64 PNG.\n[TOOLS] toolset-image",
            "agforge", "FreeForge/create-x")


class Client:
    """handle_runcreate only writes; the chatlog is never read."""


def wire(monkeypatch, tmp_path, calls, *, chosen=WORK, answer="made it",
         result_writes=(), fails=False):
    monkeypatch.setattr(runcreate_topic, "AGENTWS_ROOT", tmp_path / "agentws")
    # A test-owned toolset library: nothing here depends on which toolsets
    # the repository happens to ship.
    library = tmp_path / "toolsets"
    library.mkdir(exist_ok=True)
    (library / "toolset-image.md").write_text("# Description\nImages\n")
    (library / "toolset-video.md").write_text("# Description\nVideo\n")
    monkeypatch.setattr(toolsets, "TOOLSETS_DIR", library)
    monkeypatch.setattr(runcreate_topic, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        runcreate_topic,
        "topic_write",
        lambda topic, text, **kwargs: calls.append(("write", topic, text)) or "success",
    )
    monkeypatch.setattr(runcreate_topic, "next_work", lambda: chosen)

    def generator_run(workspace):
        calls.append(("generator", workspace))
        for name, body in result_writes:
            (workspace / "result" / name).write_text(body)
        if fails:
            (workspace / runcreate_topic.FAILURE_FLAG).write_text("")
        return answer

    monkeypatch.setattr(runcreate_topic, "run_generator", generator_run)
    monkeypatch.setattr(
        runcreate_topic,
        "report_work",
        lambda pid, iid, report, success: (
            calls.append(("report", pid, iid, report, success)) or ("F2-6", True, True)
        ),
    )
    monkeypatch.setattr(
        runcreate_topic,
        "upload_result",
        lambda archive: calls.append(("upload", archive)) or "http://minio/presigned",
    )


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
    assert [call[0] for call in calls] == [
        "write", "generator", "write", "report", "write",
    ]
    assert calls[1][1] == workspace
    # The [TOOLS] footer is addressed to prepare_workspace, not the agent:
    # it names tools/ and never reaches plan.md.
    assert (workspace / "plan.md").read_text() == "# Draw the bird\n\nOne 64x64 PNG.\n"
    assert [p.name for p in (workspace / "tools").iterdir()] == ["toolset-image.md"]
    assert (workspace / "result").is_dir()
    assert (workspace / "intermediate").is_dir()
    summary = calls[-1][2]
    assert 'running "Draw the bird"' in summary
    assert "delivered to FreeForge/create-x" in summary


# --- (b') result delivery ---------------------------------------------------


def test_an_empty_result_delivers_the_answer_text_to_the_origin(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    origin_posts = [c for c in calls if c[0] == "write" and c[1] == "create-x"]
    assert origin_posts == [("write", "create-x", "made it")]
    assert not any(c[0] == "upload" for c in calls)
    assert ("report", "p-free", "issue-1", "made it", True) in calls
    assert "result/ is empty" in calls[-1][2]


def test_a_nonempty_result_ships_as_a_zip_url(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls,
         result_writes=(("bird.png", "png bytes"), ("notes.txt", "how it went")))
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    workspace = ws(tmp_path)
    archive = next(c[1] for c in calls if c[0] == "upload")
    assert archive == workspace / "result.zip"
    assert sorted(zipfile.ZipFile(archive).namelist()) == ["bird.png", "notes.txt"]
    origin_posts = [c for c in calls if c[0] == "write" and c[1] == "create-x"]
    assert len(origin_posts) == 1
    assert "http://minio/presigned" in origin_posts[0][2]
    # The Plane comment is the generator's answer; a presigned URL expires
    # and does not belong in a permanent record.
    assert ("report", "p-free", "issue-1", "made it", True) in calls
    assert "zipped and uploaded" in calls[-1][2]


def test_the_zip_never_contains_itself(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls, result_writes=(("bird.png", "png bytes"),))
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    archive = ws(tmp_path) / "result.zip"
    assert zipfile.ZipFile(archive).namelist() == ["bird.png"]


def test_a_work_without_origin_keeps_the_result_in_the_summary(monkeypatch, tmp_path):
    handmade = Work("p-free", "issue-1", "Draw the bird", "One 64x64 PNG.", "", "")
    calls = []
    wire(monkeypatch, tmp_path, calls, chosen=handmade)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    assert [c[1] for c in calls if c[0] == "write"] == [TOPIC, TOPIC]
    summary = calls[-1][2]
    assert "no origin topic recorded" in summary
    assert "made it" in summary
    assert ("report", "p-free", "issue-1", "made it", True) in calls


def test_a_failed_origin_post_still_reports_and_summarizes(monkeypatch, tmp_path):
    """The summary post must survive everything, a dead origin included —
    and the Work still executed, so Plane is still written."""
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def flaky_write(topic, text, **kwargs):
        if topic == "create-x":
            raise RuntimeError("channel gone")
        calls.append(("write", topic, text))
        return "success"

    monkeypatch.setattr(runcreate_topic, "topic_write", flaky_write)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    summary = calls[-1][2]
    assert "could not deliver to FreeForge/create-x" in summary
    assert "made it" in summary
    assert ("report", "p-free", "issue-1", "made it", True) in calls


def test_a_retrigger_overwrites_in_place_and_keeps_results(monkeypatch, tmp_path):
    """Persistent workspace, no dirty check: plan.md and tools/ are rebuilt,
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


# --- (b'') the [TOOLS] footer and failure.flag ------------------------------


def test_a_work_without_the_footer_gets_the_whole_library(monkeypatch, tmp_path):
    """Hand-made Works, and every Work made before this phase, carry no
    footer. Giving them everything is what keeps them executable."""
    handmade = Work("p-free", "issue-1", "Draw the bird", "One 64x64 PNG.", "", "")
    calls = []
    wire(monkeypatch, tmp_path, calls, chosen=handmade)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert sorted(p.name for p in (ws(tmp_path) / "tools").iterdir()) == [
        "toolset-image.md", "toolset-video.md",
    ]


def test_an_unknown_footer_name_is_skipped_not_fatal(monkeypatch, tmp_path):
    work = Work("p-free", "issue-1", "Draw the bird",
                "One 64x64 PNG.\n[TOOLS] toolset-image, toolset-gone",
                "agforge", "FreeForge/create-x")
    calls = []
    wire(monkeypatch, tmp_path, calls, chosen=work)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert [p.name for p in (ws(tmp_path) / "tools").iterdir()] == ["toolset-image.md"]
    assert any(call[0] == "generator" for call in calls)


def test_a_retrigger_rebuilds_tools_from_the_current_footer(monkeypatch, tmp_path):
    """`tools/` is derived from the Work, like plan.md — a toolset dropped
    from the footer must not linger from the previous run."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    (ws(tmp_path) / "tools" / "toolset-video.md").write_text("stale")

    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert [p.name for p in (ws(tmp_path) / "tools").iterdir()] == ["toolset-image.md"]


def test_failure_flag_makes_the_run_a_failure(monkeypatch, tmp_path):
    """The exit code is the first-class signal; the flag is the agent's own
    verdict on top of it. `success=False` keeps the Work selectable."""
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    assert ("report", "p-free", "issue-1", "made it", False) in calls
    assert runcreate_topic.FAILURE_FLAG in calls[-1][2]
    origin_post = next(c for c in calls if c[0] == "write" and c[1] == "create-x")
    assert origin_post[2].startswith(runcreate_topic.FAILED_PREFIX)
    assert "made it" in origin_post[2]


def test_a_leftover_flag_does_not_fail_the_next_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)

    wire(monkeypatch, tmp_path, calls, fails=False)
    runcreate_topic.handle_runcreate(Client(), CHANNEL, TOPIC)
    assert ("report", "p-free", "issue-1", "made it", True) in calls
    assert not (ws(tmp_path) / runcreate_topic.FAILURE_FLAG).exists()


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
