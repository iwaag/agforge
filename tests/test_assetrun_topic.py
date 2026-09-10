"""The assetrun- topic: the request it was opened for, run on request.

The serving *discipline* — ack first, always answer, name the failed step,
the reply that hands the turn back — lives in `agag.topics` and is tested
there. What is pinned here is agforge's own part: that the request and the
origin come off the topic's selfnotes and the record they name rather than
from a queue, that the chatlog reaches the generator, the workspace shape
(persistent, overwrite-in-place, no dirty check, frozen while a job is
pending), the delivery to both topics, and `dispatch`'s routing. Nothing
asserts what an agent said.

Since `refactor` p2 the record is the conversation, so the fixture is a
realm (`realm.Realm`): a plan is posted, a request has an anchor id, and a
topic can be renamed under a run that is still holding a job.
"""

import json

import pytest
from agag import topics

from agforge import assetrun_topic, record, toolsets, zulip_listener

from realm import BOT_ID, HUMAN_ID, Realm

CHANNEL = "FreeForge"
ORIGIN_TOPIC = "assetplan-x"
PLAN = "# Draw the bird\n\nOne 64x64 PNG."


def message(sender_id=HUMAN_ID, name="Developer", content="go", id=1, topic=None):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": CHANNEL,
        "subject": topic,
        "content": content,
    }


class Client(Realm):
    """A realm holding one planned request and the run topic opened for it.

    Building it posts the record — an anchor, a plan, its notes and the run
    topic's own — the way the assetplan flow does. Only what happens
    *after* that reaches `tracker`, so a test still reads as the story of one
    trigger.
    """

    def __init__(self, calls=None, tools=("toolset-image",), said="go", plan=PLAN):
        super().__init__({(CHANNEL, ORIGIN_TOPIC): [
            message(content="make me a bird", id=1, topic=ORIGIN_TOPIC)]})
        self.tracker = None
        self.late_message = None
        self.reads = 0
        request = record.ensure_request(self, CHANNEL, ORIGIN_TOPIC, BOT_ID)
        self.request = record.record_plan(self, request, plan, tools)
        self.run = record.open_run(self, self.request, BOT_ID)
        self.topic = self.run.topic
        if said is not None:
            self.speak(said)
        #: A post that lands *after* the serving began — the run topic stays
        #: open while a generation takes its minutes — is set by the test.
        self.reads = 0
        self.tracker = [] if calls is None else calls

    def speak(self, said, sender_id=HUMAN_ID, name="Developer"):
        """Somebody other than forge posts in the run topic."""
        self.send_to_channel(CHANNEL, self.topic, said)
        self.histories[(CHANNEL, self.topic)][-1].update(
            {"sender_id": sender_id, "sender_full_name": name})

    def topic_history(self, channel, topic, num_before=50):
        self.reads += 1
        history = super().topic_history(channel, topic, num_before)
        if self.late_message is not None and self.reads > 1 and topic == self.topic:
            return [*history, self.late_message]
        return history

    def send_to_channel(self, channel, topic, content):
        if self.tracker is not None:
            self.tracker.append(("write", topic, content))
        return super().send_to_channel(channel, topic, content)

    def state_of(self, key):
        """The newest state note in one of the two conversations."""
        found = (record.read_request(self, CHANNEL, ORIGIN_TOPIC, BOT_ID) if key == "request"
                 else record.read_run(self, CHANNEL, self.topic, BOT_ID))
        return None if found is None else found.state


def wire(monkeypatch, tmp_path, calls, *, answer="made it",
         result_writes=(), fails=False, pending=None):
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

    def generator_run(workspace):
        calls.append(("generator", workspace))
        for name, body in result_writes:
            (workspace / "result" / name).write_text(body)
        if fails:
            (workspace / assetrun_topic.FAILURE_FLAG).write_text("")
        if pending is not None:
            (workspace / assetrun_topic.PENDING_FILE).write_text(json.dumps(pending))
        return answer

    monkeypatch.setattr(assetrun_topic, "run_generator", generator_run)
    monkeypatch.setattr(
        assetrun_topic,
        "upload_result",
        lambda archive: (
            calls.append(("upload", archive))
            or ("files/2026-08-15/deadbeef.zip", "http://minio/presigned")
        ),
    )


def visible(calls, topic):
    """The posts a person sees in a topic: selfnotes are not conversation."""
    return [c[2] for c in calls
            if c[0] == "write" and c[1] == topic and not c[2].startswith("[selfnote]")]


def ws(tmp_path, client):
    return tmp_path / "agentws" / client.run.label / "generator"


# --- (a) a topic that says nothing about itself ----------------------------


def test_an_unanchored_topic_is_answered_and_runs_nothing(monkeypatch, tmp_path):
    """Since p8 an `assetrun-` topic is opened by the plan that owns it, so a
    hand-made name has nothing to run — and is told so rather than handed
    whatever the queue would have picked."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    client.histories[(CHANNEL, "assetrun-handmade")] = [
        message(id=99, content="go", topic="assetrun-handmade")]

    assetrun_topic.handle_assetrun(client, CHANNEL, "assetrun-handmade")

    assert [call[0] for call in calls] == ["write", "write"]
    assert calls[0][1:] == ("assetrun-handmade", assetrun_topic.SWEEP_ACK)
    assert assetrun_topic.UNANCHORED_REPLY in calls[1][2]
    assert not (tmp_path / "agentws").exists()


def test_a_request_that_is_gone_is_said_not_guessed_at(monkeypatch, tmp_path):
    """The anchor is deleted, so the request is **absent** — not whatever now
    wears the topic name it used to have. That difference is the whole reason
    identity is a message id."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    client.delete(client.request.anchor_id)

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert f"the request this topic runs ({client.request.label}) is gone" in calls[-1][2]
    assert not any(call[0] == "generator" for call in calls)


def test_a_request_with_no_plan_yet_is_not_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    # An anchor with no plan post is a conversation that was never planned.
    client.histories[(CHANNEL, ORIGIN_TOPIC)] = [
        post for post in client.histories[(CHANNEL, ORIGIN_TOPIC)]
        if "[selfnote][doc]" not in post["content"]]

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert "has no plan recorded yet" in calls[-1][2]
    assert not any(call[0] == "generator" for call in calls)


# --- (b) the success path ---------------------------------------------------


def test_success_builds_the_workspace_runs_and_summarizes(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    workspace = ws(tmp_path, client)
    assert [call[0] for call in calls] == [
        # ack, the run, the delivery, the two result-record writes, the reply
        "write", "generator", "write", "write", "write", "write",
    ]
    assert calls[1][1] == workspace
    # `plan.md` is the recorded plan post, verbatim: the document the
    # generator wrote and the requester read.
    assert (workspace / "plan.md").read_text() == PLAN
    assert [p.name for p in (workspace / "tools").iterdir()] == ["toolset-image.md"]
    assert (workspace / "result").is_dir()
    assert (workspace / "intermediate").is_dir()
    summary = calls[-1][2]
    assert 'running "Draw the bird"' in summary
    assert f"delivered to {CHANNEL}/{ORIGIN_TOPIC}" in summary


def test_the_trigger_post_reaches_the_generator_as_a_chatlog(monkeypatch, tmp_path):
    """The button became a conversation: what the poster said is input, and
    the selfnotes that carry the wiring are not."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, said="go, but make it blue")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    chatlog = (ws(tmp_path, client) / "chatlog.md").read_text()
    # The topic's own opening line and then what the trigger said. The
    # selfnotes that carry the wiring are not conversation and are not there.
    assert chatlog.endswith("[Developer] go, but make it blue\n")
    assert "selfnote" not in chatlog


def test_the_result_reaches_both_topics_and_only_one_names_the_trigger(
    monkeypatch, tmp_path
):
    """The plan topic gets the delivery, which names the trigger and is the
    post they were waiting for. The run topic gets the record, which names
    nobody — `agent_standardize` p9, one callback per delivery."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    posted = [(c[1], c[2]) for c in calls if c[0] == "write"]
    assert posted[0][0] == client.topic          # the ack
    assert posted[1][0] == ORIGIN_TOPIC          # the delivery
    assert posted[1][1].startswith("@**Developer**")
    assert posted[-1][0] == client.topic         # the record of the run
    assert "@**Developer**" not in posted[-1][1]
    assert f"delivered to {CHANNEL}/{ORIGIN_TOPIC}" in posted[-1][1]


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
    client = Client(calls, said="go ahead")
    # Somebody else looks in *after* the serving began; the topic now ends
    # with their post, but the run was not served with it.
    client.late_message = message(
        id=9, content="how is it going?", name="Front", sender_id=15,
        topic=client.topic)

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    delivery = [c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC][0]
    assert delivery[2].startswith("@**Developer**")
    assert "@**Front**" not in delivery[2]


def test_the_delivery_follows_the_request_through_a_rename(monkeypatch, tmp_path):
    """A name-based return path does not follow a rename; the anchor does.

    This is what makes one retirement/replacement route work at all: the
    request keeps its identity under whatever name it is moved to, and the
    result goes to the request that asked for it.
    """
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    client.rename_topic(client.request.anchor_id, "✔ retired-assetplan-x-a1")

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert "delivered to FreeForge/retired-assetplan-x-a1" in calls[-1][2]
    # Under the name it wears **now**, and resolved, so the post lands in the
    # conversation instead of opening a twin beside it.
    assert visible(calls, "✔ retired-assetplan-x-a1") == ["@**Developer**\n\nmade it"]


# --- (b') result delivery ---------------------------------------------------


def test_an_empty_result_delivers_the_answer_text_to_the_origin(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    # Named, because a participant of a topic is served only when a post
    # names it: this is what gives whoever triggered the run their turn back.
    assert visible(calls, ORIGIN_TOPIC) == ["@**Developer**\n\nmade it"]
    assert not any(c[0] == "upload" for c in calls)
    assert client.state_of("request") == record.REQUEST_DELIVERED
    assert client.state_of("run") == record.RUN_DELIVERED
    assert "result/ is empty" in calls[-1][2]


def test_a_nonempty_result_ships_as_a_zip_url_and_records_the_key(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls,
         result_writes=(("bird.png", "png bytes"), ("notes.txt", "how it went")))
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    workspace = ws(tmp_path, client)
    archive = next(c[1] for c in calls if c[0] == "upload")
    assert archive == workspace / "result.zip"
    assert sorted(zipfile.ZipFile(archive).namelist()) == ["bird.png", "notes.txt"]
    origin_posts = visible(calls, ORIGIN_TOPIC)
    assert len(origin_posts) == 1
    assert "http://minio/presigned" in origin_posts[0]
    # The durable half. The URL expires in an hour; whoever reads this later
    # re-signs the key through POST /api/resign.
    key = "files/2026-08-15/deadbeef.zip"
    assert origin_posts[0].endswith(f"[S3KEY] {key}")
    # And it is the record, in both conversations — never only in a URL that
    # outlives itself by an hour.
    assert record.read_request(client, CHANNEL, ORIGIN_TOPIC, BOT_ID).results == (key,)
    assert record.read_run(client, CHANNEL, client.topic, BOT_ID).results == (key,)
    assert f"zipped and uploaded as {key}" in calls[-1][2]


def test_the_zip_never_contains_itself(monkeypatch, tmp_path):
    import zipfile

    calls = []
    wire(monkeypatch, tmp_path, calls, result_writes=(("bird.png", "png bytes"),))
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    client.speak("again")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    archive = ws(tmp_path, client) / "result.zip"
    assert zipfile.ZipFile(archive).namelist() == ["bird.png"]


def test_a_failed_origin_post_still_reports_and_summarizes(monkeypatch, tmp_path):
    """The summary post must survive everything, a dead origin included —
    and the run happened, so the record is still written."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)

    def flaky_write(topic, text, **kwargs):
        if topic == ORIGIN_TOPIC:
            raise RuntimeError("channel gone")
        calls.append(("write", topic, text))
        return "success"

    monkeypatch.setattr(assetrun_topic, "topic_write", flaky_write)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    summary = calls[-1][2]
    assert f"could not deliver to {CHANNEL}/{ORIGIN_TOPIC}" in summary
    assert "made it" in summary
    assert client.state_of("run") == record.RUN_DELIVERED


def test_a_record_that_cannot_be_written_does_not_lose_the_run_report(
    monkeypatch, tmp_path
):
    """The asset is already with the requester by then. Losing the run's
    report because the record could not be written is the worse outcome, so
    the failure is said in the summary and never raised."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)

    def explode(*args, **kwargs):
        raise record.RecordError("the realm refused the post")

    monkeypatch.setattr(assetrun_topic, "set_run_state", explode)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert "the outcome could not be recorded" in calls[-1][2]
    assert any(c[1] == ORIGIN_TOPIC for c in calls if c[0] == "write")


def test_a_retrigger_overwrites_in_place_and_keeps_results(monkeypatch, tmp_path):
    """Persistent workspace, no dirty check: plan.md and tools/ are rebuilt,
    result/ and intermediate/ keep whatever an earlier run left."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    workspace = ws(tmp_path, client)
    (workspace / "result" / "bird.png").write_text("old bytes")
    (workspace / "plan.md").write_text("stale")

    client.speak("again")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert (workspace / "plan.md").read_text() == PLAN
    assert (workspace / "result" / "bird.png").read_text() == "old bytes"


def test_a_retrigger_picks_up_a_re_planned_document(monkeypatch, tmp_path):
    """The plan lives in the conversation, so re-planning is what changes
    what the next run does — there is no second system to update."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    record.record_plan(client, client.request, "# Draw two birds\n\nTwo PNGs.",
                       ["toolset-video"])

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    workspace = ws(tmp_path, client)
    assert (workspace / "plan.md").read_text() == "# Draw two birds\n\nTwo PNGs."
    assert [p.name for p in (workspace / "tools").iterdir()] == ["toolset-video.md"]


# --- (b'') the toolset selection and failure.flag ---------------------------


def test_a_request_with_no_tools_note_gets_the_whole_library(monkeypatch, tmp_path):
    """A request nobody recorded a selection for — hand-made, or from before
    this phase. Giving it everything is what keeps it executable."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    client.histories[(CHANNEL, ORIGIN_TOPIC)] = [
        post for post in client.histories[(CHANNEL, ORIGIN_TOPIC)]
        if "[selfnote][tools]" not in post["content"]]

    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert sorted(p.name for p in (ws(tmp_path, client) / "tools").iterdir()) == [
        "toolset-image.md", "toolset-video.md",
    ]


def test_a_recorded_selection_of_none_is_not_the_whole_library(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, tools=())
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert list((ws(tmp_path, client) / "tools").iterdir()) == []


def test_an_unknown_toolset_name_is_skipped_not_fatal(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, tools=("toolset-image", "toolset-gone"))
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert [p.name for p in (ws(tmp_path, client) / "tools").iterdir()] == [
        "toolset-image.md"]
    assert any(call[0] == "generator" for call in calls)


def test_a_retrigger_rebuilds_tools_from_the_current_selection(monkeypatch, tmp_path):
    """`tools/` is derived from the record, like plan.md — a toolset dropped
    from the selection must not linger from the previous run."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    (ws(tmp_path, client) / "tools" / "toolset-video.md").write_text("stale")

    client.speak("again")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert [p.name for p in (ws(tmp_path, client) / "tools").iterdir()] == [
        "toolset-image.md"]


def test_failure_flag_makes_the_run_a_failure(monkeypatch, tmp_path):
    """The exit code is the first-class signal; the flag is the agent's own
    verdict on top of it."""
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert client.state_of("run") == record.RUN_FAILED
    assert client.state_of("request") == record.REQUEST_FAILED
    assert assetrun_topic.FAILURE_FLAG in calls[-1][2]
    origin_post = next(c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC)
    assert assetrun_topic.FAILED_PREFIX in origin_post[2]
    assert "made it" in origin_post[2]


def test_a_fresh_attempt_after_a_failure_does_not_inherit_the_old_verdict(
    monkeypatch, tmp_path
):
    """The newest state note wins, in both conversations. A request that
    failed once and then succeeded reads as delivered, and the reverse reads
    as failed — neither is read through the other."""
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True)
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert client.state_of("request") == record.REQUEST_FAILED

    wire(monkeypatch, tmp_path, calls, fails=False)
    client.speak("try again")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert client.state_of("request") == record.REQUEST_DELIVERED
    assert client.state_of("run") == record.RUN_DELIVERED
    assert not (ws(tmp_path, client) / assetrun_topic.FAILURE_FLAG).exists()


# --- (b''') the asynchronous generation hand-off ----------------------------


def test_a_queued_job_is_handed_to_the_notifier_and_the_request_stays_open(
    monkeypatch, tmp_path
):
    """The generator cannot post — it has no `agentchat` and no Zulip
    credentials by design — so `pending.json` is how it says "queued". This
    module does the talking: the reply *is* the notifier command, nothing is
    delivered, and the request is not closed."""
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": "apple relay test"})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    reply = calls[-1][2]
    assert "@**Comfy Notifier** watch b09133ad-5f47 apple relay test" in reply
    assert "```" not in reply  # a fenced mention is not a mention
    assert client.state_of("run") == record.RUN_PENDING
    assert client.state_of("request") == record.REQUEST_PLANNED  # still open
    assert not any(c[0] == "upload" for c in calls)   # nothing is delivered
    assert not any(c[0] == "write" and c[1] == ORIGIN_TOPIC for c in calls)
    # Consumed, so a second trigger cannot ask for the same watch again.
    workspace = ws(tmp_path, client)
    assert not (workspace / assetrun_topic.PENDING_FILE).exists()
    assert (workspace / assetrun_topic.WATCHING_FILE).exists()
    # The pending job says whose work it is holding, without a lookup.
    watching = json.loads((workspace / assetrun_topic.WATCHING_FILE).read_text())
    assert watching["run"] == client.run.label
    assert watching["request"] == client.request.label


def test_the_next_run_collects_the_outputs_and_records_the_result(monkeypatch, tmp_path):
    """The notifier's callback is itself a post in this topic, so it triggers
    the run that finishes the job."""
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": ""})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    wire(monkeypatch, tmp_path, calls, result_writes=[("apple.png", "x")])
    client.speak("comfy success b09133ad", sender_id=21, name="Comfy Notifier")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    assert client.state_of("run") == record.RUN_DELIVERED
    assert any(c[0] == "upload" for c in calls)
    assert record.read_request(client, CHANNEL, ORIGIN_TOPIC, BOT_ID).results == (
        "files/2026-08-15/deadbeef.zip",)
    assert not (ws(tmp_path, client) / assetrun_topic.WATCHING_FILE).exists()


def test_a_collecting_run_keeps_the_plan_the_job_was_submitted_with(
    monkeypatch, tmp_path
):
    """The job in ComfyUI was queued against the plan as it then stood.

    Re-planning meanwhile changes the request, and it must not change the
    attempt: the collecting run answers with the outputs it asked for, not
    with a question they were never for.
    """
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": ""})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    record.record_plan(client, client.request, "# Something else\n\nA song.",
                       ["toolset-video"])
    wire(monkeypatch, tmp_path, calls, result_writes=[("apple.png", "x")])
    client.speak("comfy success b09133ad", sender_id=21, name="Comfy Notifier")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    workspace = ws(tmp_path, client)
    assert (workspace / "plan.md").read_text() == PLAN
    assert [p.name for p in (workspace / "tools").iterdir()] == ["toolset-image.md"]
    assert "its own plan and tools stand" in calls[-1][2]


def test_the_requester_survives_the_wait_and_the_notifier_is_never_named(
    monkeypatch, tmp_path
):
    """The run that collects the outputs is woken by the *notifier's*
    callback, so the last voice in the topic is a bot that cannot want
    anything. Delivering to it names a machine and leaves the person who
    asked un-served — which is what happened the first time this path ran."""
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": "calm piano loop"})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    watching = json.loads(
        (ws(tmp_path, client) / assetrun_topic.WATCHING_FILE).read_text())
    assert watching["trigger"] == "@**Developer**"

    # The collecting run: the notifier spoke last, and the guide has that run
    # delete watching.json before the delivery is composed.
    def collecting(workspace):
        (workspace / "result" / "loop.mp3").write_text("x")
        (workspace / assetrun_topic.WATCHING_FILE).unlink()
        return "collected"

    calls.clear()
    wire(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(assetrun_topic, "run_generator", collecting)
    client.speak("comfy success", sender_id=21, name="Comfy Notifier")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)

    origin_post = next(c for c in calls if c[0] == "write" and c[1] == ORIGIN_TOPIC)
    assert origin_post[2].startswith("@**Developer**")
    assert "Comfy Notifier" not in origin_post[2]


def test_a_restart_while_waiting_collects_the_same_job(monkeypatch, tmp_path):
    """The listener holds nothing in memory about a pending job: the rename
    to `watching.json` is the whole state, and it is on disk beside the
    workspace named after the run. A fresh process picks it up unchanged."""
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": "a video"})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    workspace = ws(tmp_path, client)
    submitted = (workspace / assetrun_topic.WATCHING_FILE).read_text()

    # Everything in memory is gone; the record and the workspace are not.
    restarted = Client(calls)
    restarted.histories = client.histories
    restarted.streams = client.streams
    restarted.run, restarted.request = client.run, client.request
    restarted.topic = client.topic
    assert (workspace / assetrun_topic.WATCHING_FILE).read_text() == submitted
    assert assetrun_topic.collecting_a_job(client.run)

    wire(monkeypatch, tmp_path, calls, result_writes=[("clip.mp4", "x")])
    restarted.speak("comfy success", sender_id=21, name="Comfy Notifier")
    assetrun_topic.handle_assetrun(restarted, CHANNEL, restarted.topic)

    assert restarted.state_of("run") == record.RUN_DELIVERED
    assert not (workspace / assetrun_topic.WATCHING_FILE).exists()


def test_a_replacement_does_not_collect_the_old_attempt_s_job(monkeypatch, tmp_path):
    """A replacement has its own run topic, so its own workspace: it cannot
    find the pending job, cannot deliver its outputs, and the late result
    still belongs to the request that submitted it."""
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": ""})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    old_run, old_request = client.run, client.request

    record.retire_request(client, old_request, BOT_ID)
    fresh = record.open_replacement(client, CHANNEL, ORIGIN_TOPIC, BOT_ID, old_request)
    fresh = record.record_plan(client, fresh, PLAN, ["toolset-image"])
    new_run = record.open_run(client, fresh, BOT_ID)

    assert new_run.topic != old_run.topic
    assert not assetrun_topic.collecting_a_job(new_run)
    assert assetrun_topic.collecting_a_job(old_run)

    # The old job finishes. Its result goes to the request that asked for it,
    # under the name that conversation now wears — never to the replacement.
    wire(monkeypatch, tmp_path, calls, result_writes=[("apple.png", "x")])
    client.send_to_channel(CHANNEL, f"✔ retired-{old_run.topic}-{old_run.label}",
                           "comfy success")
    client.histories[(CHANNEL, f"✔ retired-{old_run.topic}-{old_run.label}")][-1].update(
        {"sender_id": 21, "sender_full_name": "Comfy Notifier"})
    assetrun_topic.handle_assetrun(
        client, CHANNEL, f"✔ retired-{old_run.topic}-{old_run.label}")

    retired = f"retired-{ORIGIN_TOPIC}-{old_request.label}"
    assert record.read_request(client, CHANNEL, retired, BOT_ID).results == (
        "files/2026-08-15/deadbeef.zip",)
    assert record.read_request(client, CHANNEL, ORIGIN_TOPIC, BOT_ID).results == ()


def test_a_run_that_queued_and_then_failed_is_a_failure_not_a_wait(monkeypatch, tmp_path):
    """`failure.flag` wins: a job may have been queued before the run knew it
    could not finish, and waiting for a notifier that will report a job
    nobody wants is worse than saying so now."""
    calls = []
    wire(monkeypatch, tmp_path, calls, fails=True,
         pending={"prompt_id": "b09133ad-5f47", "note": ""})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert client.state_of("run") == record.RUN_FAILED
    assert not any("@**Comfy Notifier**" in c[2] for c in calls if c[0] == "write")


def test_a_leftover_pending_file_does_not_re_watch_the_next_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls,
         pending={"prompt_id": "b09133ad-5f47", "note": ""})
    client = Client(calls)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    # Hand-written leftover from some earlier run.
    (ws(tmp_path, client) / assetrun_topic.PENDING_FILE).write_text('{"prompt_id": "stale"}')

    wire(monkeypatch, tmp_path, calls, result_writes=[("apple.png", "x")])
    client.speak("comfy success", sender_id=21, name="Comfy Notifier")
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert not any("stale" in c[2] for c in calls if c[0] == "write")


# --- (c) an exception mid-way names its step --------------------------------


def test_a_generator_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)

    def explode(workspace):
        raise assetrun_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(assetrun_topic, "run_generator", explode)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert calls[-1][2].endswith("failed during generator run: claude_code timed out")


def test_a_lookup_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls)

    def explode(*args, **kwargs):
        raise RuntimeError("the realm is unreachable")

    monkeypatch.setattr(assetrun_topic, "request_of_run", explode)
    assetrun_topic.handle_assetrun(client, CHANNEL, client.topic)
    assert calls[-1][2].endswith("failed during loading the request: the realm is unreachable")


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


def test_dispatch_sends_a_plain_own_channel_question_to_the_entrance(monkeypatch):
    """Not an asset topic and not an execution topic: it is a question, and
    since p10 a run answers it rather than one canned sentence."""
    from agforge import entrance_topic

    served = []
    monkeypatch.setattr(
        entrance_topic, "handle_entrance",
        lambda client, channel, topic: served.append((channel, topic)),
    )
    monkeypatch.setattr(zulip_listener, "instance_name", lambda: "agforge-agstudio1")
    zulip_listener.dispatch(object(), "agforge-agstudio1", "question")
    assert served == [("agforge-agstudio1", "question")]
