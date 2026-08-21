"""The assetrun- topic: the Work it was opened for, run on request.

The serving *discipline* — ack first, always answer, name the failed step,
the reply that hands the turn back — lives in `agag.topics` and is tested
there. What is pinned here is agforge's own part: that the Work and the
origin come off the topic's selfnotes rather than from a queue, that the
chatlog reaches the generator, the workspace shape (persistent,
overwrite-in-place, no dirty check), the delivery to both topics, and
`dispatch`'s routing. Nothing asserts what an agent said.
"""

import pytest
from agag import topics

from agforge import assetrun_topic, toolsets, zulip_listener
from agforge.works import Work

BOT_ID = 13
HUMAN_ID = 8
CHANNEL = "FreeForge"
TOPIC = "assetrun-x"
ORIGIN_TOPIC = "assetplan-x"
WORK = Work("p-free", "issue-1", "Draw the bird",
            "One 64x64 PNG.\n[TOOLS] toolset-image",
            "agforge", "FreeForge/assetplan-x")


def message(sender_id=HUMAN_ID, name="Developer", content="go", id=1):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": CHANNEL,
        "subject": TOPIC,
        "content": content,
    }


def anchored(*extra):
    """The history of a topic agforge opened for one Work: two notes, then
    whatever anybody said in it."""
    return [
        message(BOT_ID, "Forge", f"[selfnote][rootchat] {CHANNEL}/{ORIGIN_TOPIC}", 1),
        message(BOT_ID, "Forge", "[selfnote][work] p-free/issue-1", 2),
        *extra,
    ]


class Client:
    email = "forge-bot@example.invalid"

    def __init__(self, calls=None, history=None):
        self.calls = [] if calls is None else calls
        self.history = anchored(message(id=3)) if history is None else history
        #: A post that lands *after* the serving began — the run topic stays
        #: open while a generation takes its minutes.
        self.late_message = None
        self.reads = 0

    def whoami(self):
        return {"user_id": BOT_ID, "full_name": "Forge"}

    def topic_history(self, channel, topic, num_before=50):
        self.reads += 1
        if self.late_message is not None and self.reads > 1:
            return [*self.history, self.late_message]
        return self.history


def wire(monkeypatch, tmp_path, calls, *, work=WORK, answer="made it",
         result_writes=(), fails=False):
    monkeypatch.setattr(assetrun_topic, "AGENTWS_ROOT", tmp_path / "agentws")
    # A test-owned toolset library: nothing here depends on which toolsets
    # the repository happens to ship.
    library = tmp_path / "toolsets"
    library.mkdir(exist_ok=True)
    (library / "toolset-image.md").write_text("# Description\nImages\n")
    (library / "toolset-video.md").write_text("# Description\nVideo\n")
    monkeypatch.setattr(toolsets, "TOOLSETS_DIR", library)
    monkeypatch.setattr(assetrun_topic, "RECORDS_ROOT", tmp_path / "records")
    writer = lambda topic, text, **kwargs: (
        calls.append(("write", topic, text)) or "success"
    )
    # Two names, because the ack and the reply go through the skeleton while
    # the origin delivery is this module's own post.
    monkeypatch.setattr(topics, "topic_write", writer)
    monkeypatch.setattr(assetrun_topic, "topic_write", writer)
    monkeypatch.setattr(assetrun_topic, "work_by_id", lambda pid, iid: work)

    def generator_run(workspace):
        calls.append(("generator", workspace))
        for name, body in result_writes:
            (workspace / "result" / name).write_text(body)
        if fails:
            (workspace / assetrun_topic.FAILURE_FLAG).write_text("")
        return answer

    monkeypatch.setattr(assetrun_topic, "run_generator", generator_run)
    monkeypatch.setattr(
        assetrun_topic,
        "report_work",
        lambda pid, iid, report, success: (
            calls.append(("report", pid, iid, report, success)) or ("F2-6", True, True)
        ),
    )
    monkeypatch.setattr(
        assetrun_topic,
        "upload_result",
        lambda archive: (
            calls.append(("upload", archive))
            or ("files/2026-08-15/deadbeef.zip", "http://minio/presigned")
        ),
    )


def ws(tmp_path):
    return tmp_path / "agentws" / WORK.issue_id / "generator"


# --- (a) a topic that says nothing about itself ----------------------------


def test_an_unanchored_topic_is_answered_and_runs_nothing(monkeypatch, tmp_path):
    """Since p8 an `assetrun-` topic is opened by the plan that owns it, so a
    hand-made name has no Work to run — and is told so rather than handed
    whatever the queue would have picked."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, history=[message(id=1)])
    assetrun_topic.handle_assetrun(client, CHANNEL, TOPIC)
    assert [call[0] for call in calls] == ["write", "write"]
    assert calls[0][1:] == (TOPIC, assetrun_topic.SWEEP_ACK)
    assert calls[1][1] == TOPIC
    assert assetrun_topic.UNANCHORED_REPLY in calls[1][2]
    assert not (tmp_path / "agentws").exists()


def test_a_work_that_is_gone_is_said_not_guessed_at(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, work=None)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert "is gone from Plane" in calls[-1][2]
    assert not any(call[0] == "generator" for call in calls)


# --- (b) the success path ---------------------------------------------------


def test_success_builds_the_workspace_runs_and_summarizes(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

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
    assert "delivered to FreeForge/assetplan-x" in summary


def test_the_trigger_post_reaches_the_generator_as_a_chatlog(monkeypatch, tmp_path):
    """The button became a conversation: what the poster said is input, and
    the selfnotes that carry the wiring are not."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = anchored(message(id=3, content="go, but make it blue"))
    assetrun_topic.handle_assetrun(Client(calls, history), CHANNEL, TOPIC)

    chatlog = (ws(tmp_path) / "chatlog.md").read_text()
    assert chatlog == "[Developer] go, but make it blue\n"
    assert "selfnote" not in chatlog


def test_the_result_reaches_both_topics_and_only_one_names_the_trigger(
    monkeypatch, tmp_path
):
    """The plan topic gets the delivery, which names the trigger and is the
    post they were waiting for. The run topic gets the record, which names
    nobody — `agent_standardize` p9, one callback per delivery."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    posted = [(c[1], c[2]) for c in calls if c[0] == "write"]
    assert [topic for topic, _ in posted] == [TOPIC, ORIGIN_TOPIC, TOPIC]
    assert posted[1][1].startswith("@**Developer**")
    assert "@**Developer**" not in posted[2][1]
    assert f"delivered to {CHANNEL}/{ORIGIN_TOPIC}" in posted[2][1]


def test_the_delivery_names_the_trigger_not_whoever_looked_in_last(
    monkeypatch, tmp_path
):
    """A generation takes minutes and the run topic stays open while it runs.

    `agent_standardize` p9 watched a supervisor post "how is it going?" into a
    run topic mid-generation and collect the delivery meant for the agent that
    triggered it — which was then never called back, and the exchange stopped
    there. The trigger is read from the history the run was served with.
    """
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = anchored(
        message(id=3, content="go ahead", name="Developer", sender_id=HUMAN_ID),
    )
    client = Client(calls, history)
    # Somebody else looks in *after* the serving began; the topic now ends
    # with their post, but the run was not served with it.
    client.late_message = message(
        id=9, content="how is it going?", name="Front", sender_id=15
    )
    assetrun_topic.handle_assetrun(client, CHANNEL, TOPIC)

    delivery = [c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC][0]
    assert delivery[2].startswith("@**Developer**")
    assert "@**Front**" not in delivery[2]


def test_the_origin_comes_from_the_root_note_not_the_work_key(monkeypatch, tmp_path):
    """The note is what forge wrote when it opened this topic; the external
    key only answers for a Work planned before p8."""
    calls = []
    moved = Work("p-free", "issue-1", "Draw the bird",
                 "One 64x64 PNG.\n[TOOLS] toolset-image",
                 "agforge", "FreeForge/assetplan-somewhere-else")
    wire(monkeypatch, tmp_path, calls, work=moved)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert [c[1] for c in calls if c[0] == "write"] == [TOPIC, ORIGIN_TOPIC, TOPIC]


# --- (b') result delivery ---------------------------------------------------


def test_an_empty_result_delivers_the_answer_text_to_the_origin(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    origin_posts = [c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC]
    # Named, because a participant of a topic is served only when a post
    # names it: this is what gives whoever triggered the run their turn back.
    assert origin_posts == [("write", ORIGIN_TOPIC, "@**Developer**\n\nmade it")]
    assert not any(c[0] == "upload" for c in calls)
    assert ("report", "p-free", "issue-1", "made it", True) in calls
    assert "result/ is empty" in calls[-1][2]


def test_a_nonempty_result_ships_as_a_zip_url(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls,
         result_writes=(("bird.png", "png bytes"), ("notes.txt", "how it went")))
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    workspace = ws(tmp_path)
    archive = next(c[1] for c in calls if c[0] == "upload")
    assert archive == workspace / "result.zip"
    assert sorted(zipfile.ZipFile(archive).namelist()) == ["bird.png", "notes.txt"]
    origin_posts = [c for c in calls if c[0] == "write" and c[1] == "assetplan-x"]
    assert len(origin_posts) == 1
    assert "http://minio/presigned" in origin_posts[0][2]
    # The durable half. The URL expires in an hour; whoever reads this later
    # re-signs the key through POST /api/resign.
    footer = "[S3KEY] files/2026-08-15/deadbeef.zip"
    assert origin_posts[0][2].endswith(footer)
    # The Plane comment is the permanent record, so it carries the key too —
    # never only the URL that outlives it by an hour.
    assert ("report", "p-free", "issue-1", f"made it\n\n{footer}", True) in calls
    assert "zipped and uploaded as files/2026-08-15/deadbeef.zip" in calls[-1][2]


def test_the_zip_never_contains_itself(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls, result_writes=(("bird.png", "png bytes"),))
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    archive = ws(tmp_path) / "result.zip"
    assert zipfile.ZipFile(archive).namelist() == ["bird.png"]


def test_a_work_without_origin_keeps_the_result_in_the_summary(monkeypatch, tmp_path):
    """No root note and no external key: nowhere to deliver, so the summary
    carries the result rather than losing it."""
    handmade = Work("p-free", "issue-1", "Draw the bird", "One 64x64 PNG.", "", "")
    calls = []
    wire(monkeypatch, tmp_path, calls, work=handmade)
    history = [message(BOT_ID, "Forge", "[selfnote][work] p-free/issue-1", 1),
               message(id=2)]
    assetrun_topic.handle_assetrun(Client(calls, history), CHANNEL, TOPIC)

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
        if topic == "assetplan-x":
            raise RuntimeError("channel gone")
        calls.append(("write", topic, text))
        return "success"

    monkeypatch.setattr(assetrun_topic, "topic_write", flaky_write)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    summary = calls[-1][2]
    assert "could not deliver to FreeForge/assetplan-x" in summary
    assert "made it" in summary
    assert ("report", "p-free", "issue-1", "made it", True) in calls


def test_a_retrigger_overwrites_in_place_and_keeps_results(monkeypatch, tmp_path):
    """Persistent workspace, no dirty check: plan.md and tools/ are rebuilt,
    result/ and intermediate/ keep whatever an earlier run left."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    workspace = ws(tmp_path)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    (workspace / "result" / "bird.png").write_text("old bytes")
    (workspace / "plan.md").write_text("stale")

    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    assert (workspace / "plan.md").read_text() == "# Draw the bird\n\nOne 64x64 PNG.\n"
    assert (workspace / "result" / "bird.png").read_text() == "old bytes"


# --- (b'') the [TOOLS] footer and failure.flag ------------------------------


def test_a_work_without_the_footer_gets_the_whole_library(monkeypatch, tmp_path):
    """Hand-made Works, and every Work made before this phase, carry no
    footer. Giving them everything is what keeps them executable."""
    handmade = Work("p-free", "issue-1", "Draw the bird", "One 64x64 PNG.", "", "")
    calls = []
    wire(monkeypatch, tmp_path, calls, work=handmade)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert sorted(p.name for p in (ws(tmp_path) / "tools").iterdir()) == [
        "toolset-image.md", "toolset-video.md",
    ]


def test_an_unknown_footer_name_is_skipped_not_fatal(monkeypatch, tmp_path):
    work = Work("p-free", "issue-1", "Draw the bird",
                "One 64x64 PNG.\n[TOOLS] toolset-image, toolset-gone",
                "agforge", "FreeForge/assetplan-x")
    calls = []
    wire(monkeypatch, tmp_path, calls, work=work)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert [p.name for p in (ws(tmp_path) / "tools").iterdir()] == ["toolset-image.md"]
    assert any(call[0] == "generator" for call in calls)


def test_a_retrigger_rebuilds_tools_from_the_current_footer(monkeypatch, tmp_path):
    """`tools/` is derived from the Work, like plan.md — a toolset dropped
    from the footer must not linger from the previous run."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    (ws(tmp_path) / "tools" / "toolset-video.md").write_text("stale")

    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert [p.name for p in (ws(tmp_path) / "tools").iterdir()] == ["toolset-image.md"]


def test_failure_flag_makes_the_run_a_failure(monkeypatch, tmp_path):
    """The exit code is the first-class signal; the flag is the agent's own
    verdict on top of it. `success=False` keeps the Work selectable."""
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    assert ("report", "p-free", "issue-1", "made it", False) in calls
    assert assetrun_topic.FAILURE_FLAG in calls[-1][2]
    origin_post = next(c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC)
    assert assetrun_topic.FAILED_PREFIX in origin_post[2]
    assert "made it" in origin_post[2]


def test_a_leftover_flag_does_not_fail_the_next_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)

    wire(monkeypatch, tmp_path, calls, fails=False)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert ("report", "p-free", "issue-1", "made it", True) in calls
    assert not (ws(tmp_path) / assetrun_topic.FAILURE_FLAG).exists()


# --- (c) an exception mid-way names its step --------------------------------


def test_a_generator_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(workspace):
        raise assetrun_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(assetrun_topic, "run_generator", explode)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert calls[-1][2].endswith("failed during generator run: claude_code timed out")


def test_a_lookup_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(project_id, issue_id):
        raise RuntimeError("plane is down")

    monkeypatch.setattr(assetrun_topic, "work_by_id", explode)
    assetrun_topic.handle_assetrun(Client(calls), CHANNEL, TOPIC)
    assert calls[-1][2].endswith("failed during loading the work: plane is down")


# --- (d) dispatch routing ---------------------------------------------------


@pytest.mark.parametrize("topic,expected", [
    ("assetplan-20260815-x", "create"),
    ("assetrun-20260815", "assetrun"),
])
def test_dispatch_routes_by_prefix(monkeypatch, topic, expected):
    routed = []
    monkeypatch.setattr(
        "agforge.assetplan_topic.handle_topic",
        lambda client, channel, t: routed.append("create"),
    )
    monkeypatch.setattr(
        "agforge.assetrun_topic.handle_assetrun",
        lambda client, channel, t: routed.append("assetrun"),
    )
    zulip_listener.dispatch(Client(), CHANNEL, topic)
    assert routed == [expected]


def test_the_sweep_covers_both_prefixes():
    assert zulip_listener.SWEEP_PREFIXES == ("assetrun-", "assetplan-")


def test_own_channel_sweeps_every_topic_but_other_channels_keep_prefixes(monkeypatch):
    monkeypatch.setattr(zulip_listener, "instance_name", lambda: "agforge-agstudio1")
    assert zulip_listener.topic_filter("agforge-agstudio1", "a plain question")
    assert zulip_listener.topic_filter("general", "assetplan-a-request")
    assert not zulip_listener.topic_filter("general", "a plain question")


def test_dispatch_answers_a_plain_own_channel_question(monkeypatch):
    sent = []

    class EntranceClient:
        def send_to_channel(self, channel, topic, text):
            sent.append((channel, topic, text))

    monkeypatch.setattr(zulip_listener, "instance_name", lambda: "agforge-agstudio1")
    zulip_listener.dispatch(EntranceClient(), "agforge-agstudio1", "question")
    assert sent == [
        (
            "agforge-agstudio1",
            "question",
            "This is agforge-agstudio1, an asset-generation agent. "
            "To request an asset, open an `assetplan-…` topic in `agforge-agstudio1`.",
        )
    ]
