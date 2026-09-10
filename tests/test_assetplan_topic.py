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

from agforge import assetplan_topic, record, toolsets

from realm import BOT_ID, HUMAN_ID, Realm

CHANNEL = "FreeForge"
TOPIC = "assetplan-20260814-120000-abc"
STEM = "20260814-120000-abc"


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


class Client(Realm):
    """The realm this request happens in, with every call recorded in order.

    Since `refactor` p2 the record *is* the conversation, so the fixture has
    to be one: `register_plan` and `open_assetrun` write posts here and read
    them back, rather than talking to a second system a stub could stand in
    for.
    """

    def __init__(self, calls, history=None):
        super().__init__({(CHANNEL, TOPIC): list(
            [message()] if history is None else history)})
        self.tracker = calls

    def whoami(self):
        self.tracker.append(("whoami",))
        return super().whoami()

    def topic_history(self, channel, topic, num_before=50):
        self.tracker.append(("history", channel, topic, num_before))
        return super().topic_history(channel, topic, num_before)

    def send_to_channel(self, channel, topic, content):
        self.tracker.append(("write", topic, content))
        return super().send_to_channel(channel, topic, content)

    def run_topic(self):
        """The one `assetrun-` topic this request opened, by name."""
        return next(name for _, name in self.histories
                    if name.startswith("assetrun-"))


def written(calls):
    """Just the message bodies, in the order they were posted."""
    return [call[2] for call in calls if call[0] == "write"]


def wire(monkeypatch, tmp_path, calls, *, front="on it", generator="made it",
         writes_required=False, writes=(), toolsets_csv=None):
    monkeypatch.setattr(assetplan_topic, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(assetplan_topic, "RECORDS_ROOT", tmp_path / "records")
    # Posting goes through the shared skeleton, so that is where it is caught.
    writer = lambda topic, text, **kwargs: (
        calls.append(("write", topic, text)) or "success"
    )
    monkeypatch.setattr(topics, "topic_write", writer)
    # The record's own writes go through the client (`send_to_channel`), so
    # the fixture realm records them; only the skeleton's posts come through
    # this name.

    def front_run(prompt, cwd, selection=None):
        calls.append(("front", prompt, cwd, selection))
        if writes_required:
            (cwd / assetplan_topic.REQUIRED_ITEMS).write_text("one bird, blue")
        if toolsets_csv is not None:
            (cwd / assetplan_topic.TOOLSETS_CSV).write_text(toolsets_csv)
        return front

    def generator_run(cwd, selection=None):
        calls.append(("generator", cwd, selection))
        for name, body in writes:
            (cwd / name).write_text(body)
        return generator

    monkeypatch.setattr(assetplan_topic, "run_front", front_run)
    monkeypatch.setattr(assetplan_topic, "run_generator", generator_run)
    guides = tmp_path / "guides"
    (guides / "assetplan_front").mkdir(parents=True)
    (guides / "assetplan_front" / "guide.md").write_text("FRONT GUIDE")
    monkeypatch.setattr(assetplan_topic, "GUIDES", guides)
    # The toolset library the csv resolves against is the test's own, so
    # nothing here depends on which toolsets the repository happens to ship.
    library = tmp_path / "toolsets"
    library.mkdir()
    (library / "toolset-image.md").write_text("# Description\nImages\n\n# Image Tools\n")
    (library / "toolset-video.md").write_text("# Description\nVideo\n\n# Video Tools\n")
    monkeypatch.setattr(toolsets, "TOOLSETS_DIR", library)


def gen_dir(tmp_path, number, role):
    return tmp_path / "topics" / CHANNEL / TOPIC / str(number) / role


# --- (a) no required_items.md: one answer, no generator run ----------------


def test_front_only_path_acks_answers_and_stops(monkeypatch, tmp_path):
    """A question is the whole reply, and a reply names who it answers.

    Posting the front's answer early instead — which is what happens when a
    generator run follows — left it unnamed, and a requester who is not named
    is never brought back. `agent_standardize` p9 watched an exchange stop
    dead on exactly that: forge asked a question and nobody was told.
    """
    calls = []
    wire(monkeypatch, tmp_path, calls)

    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

    assert [call[0] for call in calls] == [
        # who this instance is mentioned by, for the execution-options menu;
        # then serve_topic's own whoami, the read that comes *before* the ack
        # so a configuration-only post costs neither, the ack, the front run,
        # the handoff lookup, the reply, the post-run re-check
        "whoami", "whoami", "history", "write", "front", "history", "write", "history",
    ]
    assert calls[3][1:] == (TOPIC, assetplan_topic.SWEEP_ACK)
    assert calls[6][1:] == (TOPIC, "@**Developer**\n\non it")
    # The chatlog lands in this generation's front workspace.
    assert (gen_dir(tmp_path, 1, "front") / "chatlog.md").read_text() == (
        "[Developer] make me a bird\n"
    )
    assert calls[4][2] == gen_dir(tmp_path, 1, "front")
    assert not (tmp_path / "topics" / CHANNEL / TOPIC / "1" / "generator").exists()


def test_the_front_prompt_is_the_placement_line_plus_its_own_guide(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
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
        toolsets_csv="toolset-image, Images\n",
        writes=(("idea.md", "buy a GPU"), ("plan.md", "# Bird\n\nDraw it.")),
    )
    client = Client(calls)
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    generator = gen_dir(tmp_path, 1, "generator")
    assert [call[0] for call in calls] == [
        "whoami", "whoami", "history", "write", "front", "write", "generator",
        # recording the plan: is this conversation anchored already, then the
        # anchor, the plan itself, and the three notes that describe it
        "history", "write", "write", "write", "write", "write",
        # opening the request's own run topic: read it — twice, because a
        # topic that comes back empty is read again under its ✔ name — then
        # the two selfnotes and the one visible line
        "history", "history", "write", "write", "write",
        # the handoff lookup, the reply, then the post-run re-check
        "history", "write", "history",
    ]
    assert (generator / "required_items.md").read_text() == "one bird, blue"
    assert [path.name for path in (generator / "tools").iterdir()] == ["toolset-image.md"]
    assert calls[6][1] == generator
    # The plan is recorded, the run topic is named, idea.md is relayed
    # verbatim, then the answer.
    request = record.read_request(client, CHANNEL, TOPIC, BOT_ID)
    assert request.plan == "# Bird\n\nDraw it."
    assert request.tools == ["toolset-image"]
    assert written(calls)[-1] == (
        "@**Developer**\n\n"
        f'recorded {request.label} "Bird" (toolsets: toolset-image)\n\n'
        f"posting in {client.run_topic()} starts it\n\nbuy a GPU\n\nmade it"
    )


def test_the_front_answer_is_posted_before_the_generator_runs(monkeypatch, tmp_path):
    """The front's answer is the conversational reply; the generator can take
    minutes, and the topic should not sit silent through them."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    kinds = [call[0] for call in calls]
    assert kinds.index("write", 3) < kinds.index("generator")


def test_a_plan_alone_still_reports_and_answers(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))
    client = Client(calls)
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)
    request = record.read_request(client, CHANNEL, TOPIC, BOT_ID)
    assert written(calls)[-1] == (
        "@**Developer**\n\n"
        f'recorded {request.label} "Bird" (toolsets: none)\n\n'
        f"posting in {client.run_topic()} starts it\n\nmade it"
    )


# --- (b2) toolsets.csv → tools/ --------------------------------------------


def test_the_csv_names_resolve_leniently_and_unknown_ones_are_skipped(
    monkeypatch, tmp_path
):
    """The front copies these lines out of `agforge toolsets --list` by hand,
    so extensions, description tails, blanks and case are all expected."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True, toolsets_csv=(
        "toolset-image, Images\n"
        "TOOLSET-VIDEO.md\n"
        "\n"
        "toolset-nope\n"
    ))
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert sorted(
        path.name for path in (gen_dir(tmp_path, 1, "generator") / "tools").iterdir()
    ) == ["toolset-image.md", "toolset-video.md"]


def test_no_csv_leaves_an_empty_tools_directory(monkeypatch, tmp_path):
    """Not an error: the generator guide routes this case itself — it asks
    back, writes idea.md, or declines."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    tools = gen_dir(tmp_path, 1, "generator") / "tools"
    assert tools.is_dir() and list(tools.iterdir()) == []
    assert any(call[0] == "generator" for call in calls)


# --- (b3) every reply names whoever is being answered ---------------------


def test_the_reply_names_the_last_non_forge_poster(monkeypatch, tmp_path):
    """Being named is how the next run happens at all — a participant of a
    conversation is served only when a post mentions it. So the shared
    skeleton names them on every reply, question or not, and forge stopped
    doing it itself behind `question.flag`."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True, generator="what size?")
    history = [
        message(name="Developer", content="make me a bird"),
        message(sender_id=BOT_ID, name="Forge", content="on it", id=2),
    ]

    assetplan_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)

    assert written(calls)[-1] == "@**Developer**\n\nwhat size?"


def test_the_mention_names_the_most_recent_asker(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True, generator="what size?")
    history = [
        message(name="Developer", content="make me a bird"),
        message(sender_id=99, name="Autolab", content="64x64 please", id=2),
    ]

    assetplan_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)

    assert written(calls)[-1].startswith("@**Autolab**")


def test_nobody_is_named_twice(monkeypatch, tmp_path):
    """The old `question.flag` mention is gone, so a plain plan and a question
    are named exactly once each."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True, generator="made it")
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert written(calls)[-1].count("@**") == 1


# --- (c) an exception mid-way: `failed during …` is posted -----------------


def test_a_front_failure_names_its_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)

    def explode(prompt, cwd, selection=None):
        raise assetplan_topic.ListenerError("claude_code timed out")

    monkeypatch.setattr(assetplan_topic, "run_front", explode)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert written(calls)[-1] == (
        "@**Developer**\n\nfailed during front: claude_code timed out"
    )


def test_a_generator_failure_names_its_own_step(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)

    def explode(cwd, selection=None):
        raise assetplan_topic.ListenerError("no disk space")

    monkeypatch.setattr(assetplan_topic, "run_generator", explode)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert "failed during generator: no disk space" in calls[-1][2]


def test_a_record_failure_is_reported_not_swallowed(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))

    def explode(*args):
        raise assetplan_topic.ListenerError("the realm refused the post")

    monkeypatch.setattr(assetplan_topic, "register_plan", explode)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assert "failed during generator: the realm refused the post" in calls[-1][2]


# --- generations -----------------------------------------------------------


def test_generation_increments_once_per_serve_and_keeps_the_old_ones(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)
    assetplan_topic.handle_topic(Client(calls), CHANNEL, TOPIC)

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
    assetplan_topic.handle_topic(Client(calls, history=[]), CHANNEL, TOPIC)
    assert not any(call[0] in {"front", "generator"} for call in calls)
    assert calls[-1][2] == assetplan_topic.EMPTY_REPLY


# --- agforge's own chatlog rule --------------------------------------------


def test_our_acks_are_dropped_from_the_chatlog(monkeypatch, tmp_path):
    """Leaving them in would teach the front that "please wait for the reply"
    is something it once said in answer to a request."""
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [
        message(),
        message(sender_id=BOT_ID, name="Forge", content=assetplan_topic.SWEEP_ACK),
        message(sender_id=BOT_ID, name="Forge", content=assetplan_topic.ACK_PREFIX + " (run x)"),
        message(sender_id=BOT_ID, name="Forge", content="here you go"),
    ]
    assetplan_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert (gen_dir(tmp_path, 1, "front") / "chatlog.md").read_text() == (
        "[Developer] make me a bird\n[Forge (you)] here you go\n"
    )


def test_a_human_quoting_an_ack_stays_in_the_chatlog(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    history = [message(content=assetplan_topic.SWEEP_ACK)]
    assetplan_topic.handle_topic(Client(calls, history=history), CHANNEL, TOPIC)
    assert assetplan_topic.SWEEP_ACK in (
        gen_dir(tmp_path, 1, "front") / "chatlog.md"
    ).read_text()


def test_guide_refuses_to_start_without_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(assetplan_topic, "GUIDES", tmp_path)
    with pytest.raises(GuideError):
        assetplan_topic.guide("assetplan_front", "guide.md")


# --- (c) registering the plan opens the Work's own run topic ---------------


def test_registering_opens_the_run_topic_with_its_two_anchors(monkeypatch, tmp_path):
    """The requester never invents an `assetrun-` name, and the topic that is
    opened says what it runs — that is what replaced `next_work`'s guess.

    Its name carries the request's anchor id, so a later request under the
    same stem cannot merge into it (`refactor` p2).
    """
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))

    client = Client(calls)
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    request = record.read_request(client, CHANNEL, TOPIC, BOT_ID)
    run_topic = client.run_topic()
    assert run_topic == f"assetrun-{STEM}-{request.label}"
    into_run_topic = [c[2] for c in calls if c[0] == "write" and c[1] == run_topic]
    assert into_run_topic[0] == f"[selfnote][rootchat] {CHANNEL}/{TOPIC}"
    assert into_run_topic[1] == f"[selfnote][assetrun] {request.anchor_id}"
    # Everything a reader ever sees of it is the third line.
    assert len(into_run_topic) == 3
    assert f'{request.label} "Bird"' in into_run_topic[2]
    assert TOPIC in into_run_topic[2]
    assert "selfnote" not in into_run_topic[2]


def test_a_second_generation_finds_the_run_topic_already_anchored(monkeypatch, tmp_path):
    """One request, one run topic, however far the generation number climbs."""
    calls = []
    wire(monkeypatch, tmp_path, calls, writes_required=True,
         writes=(("plan.md", "# Bird\n\nDraw it."),))
    client = Client(calls)
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)
    run_topic = client.run_topic()
    request = record.read_request(client, CHANNEL, TOPIC, BOT_ID)
    calls.clear()

    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    assert [c for c in calls if c[0] == "write" and c[1] == run_topic] == []
    assert f"posting in {run_topic} starts it" in written(calls)[-1]
    # A re-plan is the same request with a new document, not a second one.
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).anchor_id == request.anchor_id


# --- execution options (ag.exec-options.v1, refactor p3 ex1 step 2) --------

from agag import execopt  # noqa: E402
from agag.execopt import Selection  # noqa: E402

from agforge import instance  # noqa: E402


def exec_command(option, bot="Forge"):
    return f"@**{bot}** use {option}"


def test_forge_publishes_only_profiles_it_actually_has(tmp_path):
    config = tmp_path / "agents.toml"
    config.write_text(
        'schema = "ag.agent-config.v2"\n'
        '[models."antigravity/g"]\n'
        '[profiles.agy]\nharness = "agy"\nmodel = "antigravity/g"\n',
        encoding="utf-8",
    )
    names = [option.name for option in instance.exec_options(config)]
    # `agy-claude` is published only where the profile exists, and `stub` is
    # never published at all: a menu that offers the fake harness lies.
    assert names == ["default", "agy"]


def test_an_unreadable_config_publishes_nothing_rather_than_a_wrong_menu(tmp_path):
    assert instance.exec_options(tmp_path / "nothing.toml") == ()


def test_the_test_only_profiles_stay_off_the_public_menu():
    published = [name for name, _, _ in instance.PUBLIC_PROFILES]
    assert "stub" not in published and "sonnet" not in published and "local" not in published


def test_every_option_says_it_does_not_choose_the_media_model():
    # Selecting `agy` changes how forge thinks about a request, never what
    # the request is for: the media model is named by the plan's toolset.
    for option in instance.SPEC.published_options("Forge").options:
        assert "Not the media model" in option.covers
        assert "generation" in option.covers and "callback" in option.covers


def test_a_selection_reaches_the_front_and_the_planning_generator(monkeypatch, tmp_path):
    calls = []
    wire(
        monkeypatch, tmp_path, calls,
        writes_required=True,
        writes=(("plan.md", "# Bird\n\nDraw it."),),
    )
    client = Client(calls, history=[
        message(content=exec_command("agy"), id=7),
        message(content="make me a bird", id=8),
    ])
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    front = next(call for call in calls if call[0] == "front")
    generator = next(call for call in calls if call[0] == "generator")
    assert front[3].option == "agy" and front[3].source == "topic"
    assert generator[2].option == "agy"


def test_the_run_topic_inherits_the_plans_selection(monkeypatch, tmp_path):
    calls = []
    wire(
        monkeypatch, tmp_path, calls,
        writes_required=True,
        writes=(("plan.md", "# Bird\n\nDraw it."),),
    )
    client = Client(calls, history=[
        message(content=exec_command("agy"), id=7),
        message(content="make me a bird", id=8),
    ])
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    run_topic = client.run_topic()
    notes = [post["content"] for post in client.histories[(CHANNEL, run_topic)]
             if post["content"].startswith("[selfnote][exec]")]
    assert notes == [f"[selfnote][exec] agy from {CHANNEL}/{TOPIC}#7"]
    option, source, message_id = execopt.parse_exec_note(notes[0])
    assert option == "agy" and str(source) == f"{CHANNEL}/{TOPIC}" and message_id == 7


def test_a_plan_with_no_selection_writes_no_snapshot(monkeypatch, tmp_path):
    calls = []
    wire(
        monkeypatch, tmp_path, calls,
        writes_required=True,
        writes=(("plan.md", "# Bird\n\nDraw it."),),
    )
    client = Client(calls)
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)
    run_topic = client.run_topic()
    assert not [post for post in client.histories[(CHANNEL, run_topic)]
                if post["content"].startswith("[selfnote][exec]")]


def test_a_configuration_only_post_costs_no_run_and_is_answered(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, history=[message(content=exec_command("agy"), id=7)])
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    assert not [call for call in calls if call[0] in ("front", "generator")]
    assert "agy" in written(calls)[-1] and "Execution option set" in written(calls)[-1]


def test_an_unpublished_option_is_refused_and_never_becomes_the_setting(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, history=[message(content=exec_command("opus"), id=7)])
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)

    reply = written(calls)[-1]
    assert "do not publish an execution option named `opus`" in reply
    assert "`agy`" in reply
    assert execopt.resolve(
        client.histories[(CHANNEL, TOPIC)], "Forge",
        known=instance.SPEC.published_options("Forge").names,
    ) == Selection()


def test_a_reset_returns_the_topic_to_the_configured_defaults(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, tmp_path, calls)
    client = Client(calls, history=[
        message(content=exec_command("agy"), id=7),
        message(content=exec_command("default"), id=8),
        message(content="make me a bird", id=9),
    ])
    assetplan_topic.handle_topic(client, CHANNEL, TOPIC)
    front = next(call for call in calls if call[0] == "front")
    assert front[3].option is None


# --- derived usage pools (agag.execpool, refactor p3 ex1 step 3) -----------

import tomllib as _tomllib  # noqa: E402

from agag import execpool  # noqa: E402
from agag.agent_config import load_config as _load_config  # noqa: E402


def _real_config():
    return _load_config(instance.SPEC.agents_config, instance.SPEC.agents_local_config)


def test_the_named_options_resolve_to_the_pools_they_declare():
    """A declaration is an assertion, and this is that assertion checked.

    The named options depend only on the committed `agents.toml` — an
    overlay moves *roles*, not the option-to-profile mapping — so this is
    deterministic on any machine that can read the config.
    """
    declared = {o.name: o.pool for o in instance.SPEC.exec_options_with_default()}
    published = {o.name: o.pool for o in instance.SPEC.published_options("Forge").options}
    assert set(declared) == set(published)
    for name, pool in declared.items():
        if name != "default":
            assert published[name] == pool, name


def test_the_default_is_priced_from_the_roles_this_machine_will_run():
    assert instance.SPEC.published_options("Forge").get("default").pool not in ("", "-")


def test_every_covered_role_is_a_role_this_agent_has_configured():
    """`exec_roles` is what the pool is derived from, so a name that is not a
    role would silently price the menu from nothing."""
    config, _ = _real_config()
    for role in instance.SPEC.exec_roles:
        assert role in config["roles"], role


def test_a_role_moved_in_the_overlay_moves_the_derived_default(tmp_path):
    """The failure the derivation exists for, on this agent's own roles.

    One line in a machine's `agents.local.toml` sends `generator` to another
    harness. Before `refactor` p3 ex1 the published default kept saying
    `anthropic`; now it follows, and the stale declaration is named.
    """
    config, _ = _real_config()
    overlay = _tomllib.loads(
        'schema = "ag.agent-config.v2"\n[roles.generator]\nprofile = "agy"\n'
    )
    found = execpool.derive(
        None, instance.SPEC.exec_roles, config, overlay, instance.SPEC.profile_for
    )
    assert execpool.JOIN in found.pool and "antigravity" in found.pool
    declared = instance.SPEC.exec_options_with_default()[0]
    lines = execpool.diagnose([declared], [found])
    assert len(lines) == 1 and "generator -> agy/agy (antigravity)" in lines[0]


def test_an_unavailable_harness_is_not_reported_as_a_wrong_declaration(monkeypatch):
    """Availability is a runtime fact. A CLI that is not installed makes that
    one option fail when it runs; it must not read as a broken contract, and
    it must not take an unrelated conversation down."""
    real = execpool.resolve_role

    def flaky(config, overlay, role, *, profile_override=None, check_available=True):
        if check_available:
            raise execpool.AgentConfigError("E_UNAVAILABLE", "nothing is installed")
        return real(config, overlay, role, profile_override=profile_override,
                    check_available=False)

    monkeypatch.setattr(execpool, "resolve_role", flaky)
    published = instance.SPEC.published_options("Forge")
    assert published.get("default").pool not in ("", "-")
    assert instance.SPEC.pool_diagnostics() == ()
