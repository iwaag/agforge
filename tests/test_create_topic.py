"""Deterministic-shell tests for the create-topic workflow.

Same rule as the rest of the suite: nothing here asserts what an agent said.
These pin the transport around the two runs — the generation numbering, what
lands in each workspace, which branch the front's *files* select, and the
promise that every path after the ack posts something back to the topic.
"""

import pytest

from agforge import create_topic

BOT_ID = 13
HUMAN_ID = 8
CHANNEL = "FreeForge"
TOPIC = "create-20260814-120000-abc"


def message(sender_id=HUMAN_ID, name="Developer", content="make me a bird", id=1):
    return {
        "id": id,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": name,
        "display_recipient": CHANNEL,
        "subject": TOPIC,
        "content": content,
    }


class Client:
    email = "forge-bot@example.invalid"

    def __init__(self, calls, history=None):
        self.calls = calls
        self.history = history if history is not None else [message()]

    def whoami(self):
        self.calls.append(("whoami",))
        return {"user_id": BOT_ID, "full_name": "Forge"}

    def topic_history(self, channel, topic, num_before):
        self.calls.append(("history", channel, topic, num_before))
        return self.history


def wire(monkeypatch, tmp_path, calls, *, front="on it", generator="made it",
         writes_required=False, writes=()):
    monkeypatch.setattr(create_topic, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(create_topic, "RECORDS_ROOT", tmp_path / "records")
    monkeypatch.setattr(
        create_topic, "guide", lambda *parts: f"GUIDE({'/'.join(parts)})"
    )
    monkeypatch.setattr(
        create_topic,
        "topic_write",
        lambda topic, text, **kwargs: calls.append(("write", topic, text)) or "success",
    )

    def front_run(prompt, cwd):
        calls.append(("front", prompt, cwd))
        if writes_required:
            (cwd / create_topic.REQUIRED_ITEMS).write_text("one bird, blue")
        return front

    def generator_run(cwd):
        calls.append(("generator", cwd))
        for name, body in writes:
            (cwd / name).write_text(body)
        return generator

    monkeypatch.setattr(create_topic, "run_front", front_run)
    monkeypatch.setattr(create_topic, "run_generator", generator_run)
    # tools.md is copied from the real guides tree; keep that read local.
    guides = tmp_path / "guides"
    (guides / "create_generator").mkdir(parents=True)
    (guides / "create_generator" / "tools.md").write_text("## Tools\n- generate.sh\n")
    monkeypatch.setattr(create_topic, "GUIDES", guides)


def gen_dir(tmp_path, number, role):
    return tmp_path / "topics" / CHANNEL / TOPIC / str(number) / role


# --- (a) no required_items.md: one answer, no generator run ----------------


def test_front_only_path_acks_answers_and_stops(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert [call[0] for call in calls] == [
        "whoami", "write", "history", "front", "write", "history",
    ]
    # The ack is the first post, before any work: it makes the bot the last
    # poster so a later sweep skips the topic while this run is in flight.
    assert calls[1][1:] == (TOPIC, create_topic.SWEEP_ACK)
    assert calls[4][1:] == (TOPIC, "on it")
    # The chatlog lands in this generation's front workspace.
    assert (gen_dir(tmp_path, 1, "front") / "chatlog.md").read_text() == (
        "[Developer] make me a bird\n"
    )
    assert calls[3][2] == gen_dir(tmp_path, 1, "front")
    assert not (tmp_path / "topics" / CHANNEL / TOPIC / "1" / "generator").exists()


# --- (b) required_items.md present: the generator runs ---------------------


def test_required_items_builds_the_generator_workspace_and_runs_it(monkeypatch, tmp_path):
    calls = []
    wire(
        monkeypatch, tmp_path, calls,
        writes_required=True,
        writes=(("idea.md", "buy a GPU"), ("plan.md", "# Bird\n\nDraw it.")),
    )
    monkeypatch.setattr(
        create_topic,
        "register_plan",
        lambda channel, topic, plan: calls.append(("plan", plan)) or "registered PA-1",
    )

    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    generator = gen_dir(tmp_path, 1, "generator")
    assert [call[0] for call in calls] == [
        "whoami", "write", "history", "front", "write", "generator", "plan", "write",
        "history",
    ]
    # required_items.md and tools.md are what the generator is given.
    assert (generator / "required_items.md").read_text() == "one bird, blue"
    assert "generate.sh" in (generator / "tools.md").read_text()
    assert calls[5][1] == generator
    # plan.md is registered, idea.md is relayed verbatim, then the answer.
    assert calls[-2][2] == "registered PA-1\n\nbuy a GPU\n\nmade it"


def test_the_front_answer_is_posted_before_the_generator_runs(monkeypatch, tmp_path):
    """The front's answer is the conversational reply; the generator can take
    minutes, and the topic should not sit silent for them."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    kinds = [call[0] for call in calls]
    assert kinds.index("write", 3) < kinds.index("generator")


# --- (c) an exception mid-way: `failed during …` is posted -----------------


def test_a_failure_after_the_ack_is_always_reported(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd):
        raise create_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(create_topic, "run_front", explode)

    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert calls[-1][0] == "write"
    assert calls[-1][2] == "failed during front: claude_code timed out"


def test_a_generator_failure_names_its_own_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)

    def explode(cwd):
        raise create_topic.ListenerError("no disk space")

    monkeypatch.setattr(create_topic, "run_generator", explode)

    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert "failed during generator: no disk space" in calls[-1][2]


# --- generations -----------------------------------------------------------


def test_generation_increments_once_per_serve_and_keeps_the_old_ones(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert gen_dir(tmp_path, 1, "front").is_dir()
    assert gen_dir(tmp_path, 2, "front").is_dir()
    # A previous generation's required_items.md stays where it is; cutting a
    # new N is what stops it from being re-executed.
    assert (gen_dir(tmp_path, 1, "generator") / "required_items.md").is_file()
    assert [call[1] for call in calls if call[0] == "generator"] == [
        gen_dir(tmp_path, 1, "generator"),
        gen_dir(tmp_path, 2, "generator"),
    ]


def test_next_generation_reads_the_directory_not_a_counter(tmp_path):
    assert create_topic.next_generation(tmp_path) == 1
    (tmp_path / "1").mkdir()
    (tmp_path / "4").mkdir()
    (tmp_path / "notes").mkdir()  # not a generation
    assert create_topic.next_generation(tmp_path) == 5


def test_handle_topic_reprocesses_when_a_human_posted_during_the_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    first = message()
    mid_run = message(content="make it blue", id=2)

    class ScriptedClient(Client):
        def __init__(self):
            super().__init__(calls)
            self.scripts = [[first], [first, mid_run], [first, mid_run], [first, mid_run]]

        def topic_history(self, channel, topic, num_before):
            calls.append(("history", channel, topic, num_before))
            return self.scripts.pop(0)

    create_topic.handle_topic(ScriptedClient(), CHANNEL, TOPIC)

    assert [call[0] for call in calls].count("front") == 2
    # A re-serve is a new generation, with the fuller chatlog.
    assert (gen_dir(tmp_path, 2, "front") / "chatlog.md").read_text().endswith(
        "make it blue\n"
    )


# --- chatlog and prompt ----------------------------------------------------


def test_chatlog_marks_own_lines_and_drops_the_acks():
    text = create_topic.format_chatlog(
        [
            message(),
            message(sender_id=BOT_ID, name="Forge", content=create_topic.SWEEP_ACK),
            message(sender_id=BOT_ID, name="Forge", content="here you go"),
        ],
        BOT_ID,
    )
    assert text == "[Developer] make me a bird\n[Forge (you)] here you go\n"


def test_front_prompt_is_the_placement_line_plus_the_guide(monkeypatch, tmp_path):
    guide_dir = tmp_path / "create_front"
    guide_dir.mkdir(parents=True)
    (guide_dir / "guide.md").write_text("GUIDE TEXT\n")
    monkeypatch.setattr(create_topic, "GUIDES", tmp_path)
    assert create_topic.front_prompt("Forge") == (
        "The chatlog is placed in the working directory. "
        "You are 'Forge' in the chatlog.\n\nGUIDE TEXT"
    )


def test_guide_refuses_to_start_without_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(create_topic, "GUIDES", tmp_path)
    with pytest.raises(create_topic.ListenerError):
        create_topic.guide("create_front", "guide.md")


def test_topic_workspace_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(create_topic, "TOPICS_ROOT", tmp_path)
    assert create_topic.topic_workspace(CHANNEL, TOPIC) == tmp_path / CHANNEL / TOPIC
    for bad in ("../outside", "a/b", ""):
        with pytest.raises(ValueError):
            create_topic.topic_workspace(bad, TOPIC)
        with pytest.raises(ValueError):
            create_topic.topic_workspace(CHANNEL, bad)


def test_next_record_path_numbers_like_every_other_run_record(tmp_path):
    assert create_topic.next_record_path(tmp_path).name == "run-0001.json"
    (tmp_path / "run-0001.json").write_text("{}")
    assert create_topic.next_record_path(tmp_path).name == "run-0002.json"
