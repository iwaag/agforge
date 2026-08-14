"""agforge's part of serving a create topic: the front/generator shape.

The serving *discipline* — ack first, always answer, name the failed step,
re-serve when a human spoke during the run, the empty-topic guard, workspace
numbering, chatlog formatting — now lives in `agag.topics` and is tested
there. What is pinned here is only what agforge decides: which files travel
into which generation directory, which branch the front's *files* select, and
the order the answers reach the topic.

Same rule as the rest of the suite: nothing asserts what an agent said.
"""

import pytest
from agag import topics
from agag.topics import GuideError

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
        self.history = [message()] if history is None else history

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
    # Posting goes through the shared skeleton, so that is where it is caught.
    monkeypatch.setattr(
        topics,
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
    guides = tmp_path / "guides"
    (guides / "create_front").mkdir(parents=True)
    (guides / "create_front" / "guide.md").write_text("FRONT GUIDE")
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
    assert calls[1][1:] == (TOPIC, create_topic.SWEEP_ACK)
    assert calls[4][1:] == (TOPIC, "on it")
    # The chatlog lands in this generation's front workspace.
    assert (gen_dir(tmp_path, 1, "front") / "chatlog.md").read_text() == (
        "[Developer] make me a bird\n"
    )
    assert calls[3][2] == gen_dir(tmp_path, 1, "front")
    assert not (tmp_path / "topics" / CHANNEL / TOPIC / "1" / "generator").exists()


def test_the_front_prompt_is_the_placement_line_plus_its_own_guide(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    prompt = next(call[1] for call in calls if call[0] == "front")
    assert prompt == (
        "The chatlog is placed in the working directory. "
        "You are 'Forge' in the chatlog.\n\nFRONT GUIDE"
    )


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
    assert (generator / "required_items.md").read_text() == "one bird, blue"
    assert "generate.sh" in (generator / "tools.md").read_text()
    assert calls[5][1] == generator
    # plan.md is registered, idea.md is relayed verbatim, then the answer.
    assert calls[-2][2] == "registered PA-1\n\nbuy a GPU\n\nmade it"


def test_the_front_answer_is_posted_before_the_generator_runs(monkeypatch, tmp_path):
    """The front's answer is the conversational reply; the generator can take
    minutes, and the topic should not sit silent through them."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    kinds = [call[0] for call in calls]
    assert kinds.index("write", 3) < kinds.index("generator")


def test_a_plan_alone_still_reports_and_answers(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))
    monkeypatch.setattr(create_topic, "register_plan", lambda *a: "registered PA-1")
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert calls[-2][2] == "registered PA-1\n\nmade it"


# --- (c) an exception mid-way: `failed during …` is posted -----------------


def test_a_front_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd):
        raise create_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(create_topic, "run_front", explode)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert calls[-1][2] == "failed during front: claude_code timed out"


def test_a_generator_failure_names_its_own_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)

    def explode(cwd):
        raise create_topic.ListenerError("no disk space")

    monkeypatch.setattr(create_topic, "run_generator", explode)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert "failed during generator: no disk space" in calls[-1][2]


def test_a_plane_failure_is_reported_not_swallowed(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))

    def explode(channel, topic, plan):
        raise create_topic.ListenerError("plane is down")

    monkeypatch.setattr(create_topic, "register_plan", explode)
    create_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert "failed during generator: plane is down" in calls[-1][2]


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


def test_an_empty_topic_costs_no_agent_run(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    create_topic.handle_topic(Client(calls, history=[]), CHANNEL, TOPIC)
    assert not any(call[0] in {"front", "generator"} for call in calls)
    assert calls[-1][2] == create_topic.EMPTY_REPLY


# --- agforge's own chatlog rule --------------------------------------------


def test_our_acks_are_dropped_from_the_chatlog(monkeypatch, tmp_path):
    """Leaving them in would teach the front that "please wait for the reply"
    is something it once said in answer to a request."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [
        message(),
        message(sender_id=BOT_ID, name="Forge", content=create_topic.SWEEP_ACK),
        message(sender_id=BOT_ID, name="Forge", content=create_topic.ACK_PREFIX + " (run x)"),
        message(sender_id=BOT_ID, name="Forge", content="here you go"),
    ]
    create_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert (gen_dir(tmp_path, 1, "front") / "chatlog.md").read_text() == (
        "[Developer] make me a bird\n[Forge (you)] here you go\n"
    )


def test_a_human_quoting_an_ack_stays_in_the_chatlog(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [message(content=create_topic.SWEEP_ACK)]
    create_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert create_topic.SWEEP_ACK in (
        gen_dir(tmp_path, 1, "front") / "chatlog.md"
    ).read_text()


def test_guide_refuses_to_start_without_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(create_topic, "GUIDES", tmp_path)
    with pytest.raises(GuideError):
        create_topic.guide("create_front", "guide.md")
