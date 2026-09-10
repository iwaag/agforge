"""forge's work record, read and written out of the conversations.

What is pinned here is what `refactor` p2 claims: identity is a message id
and survives every rename; the plan and the toolset selection come back out
of the conversation they were written into; a state is the newest note and
never inherits an older verdict; a result is recorded as the durable object
key; and one retirement/replacement route where late replies belong to the
old request and a deleted origin is *absent* rather than the new request
under the same name.
"""

import pytest

from agforge import record
from agforge.anchor import parse_asset, parse_run

from realm import BOT_ID, HUMAN_ID, Realm, human

CHANNEL = "agforge-agstudio1"
TOPIC = "assetplan-robot"
PLAN = "# Draw the bird\n\nOne 64x64 PNG."


def realm(**kwargs):
    return Realm({(CHANNEL, TOPIC): [human("make me a bird")]}, **kwargs)


def planned(client, tools=("toolset-image",)):
    request = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    return record.record_plan(client, request, PLAN, tools)


# --- identity ---------------------------------------------------------------


def test_a_request_is_the_message_id_of_its_own_note():
    client = realm()
    request = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    anchor = client.message(request.anchor_id)
    assert parse_asset(anchor["content"]) == "robot"
    assert request.label == f"a{request.anchor_id}"


def test_anchoring_happens_once():
    client = realm()
    first = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    again = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    assert again.anchor_id == first.anchor_id
    assert sum("[selfnote][asset]" in body for body in client.posts(CHANNEL, TOPIC)) == 1


def test_a_request_is_found_through_a_rename():
    """The whole reason identity is an id: resolving a topic renames it, and
    a human may rename it again."""
    client = realm()
    request = planned(client)
    client.rename_topic(request.anchor_id, "✔ retired-assetplan-robot-a101")

    found = record.request_at(client, request.anchor_id, BOT_ID)

    assert found is not None
    assert found.anchor_id == request.anchor_id
    assert found.topic == "retired-assetplan-robot-a101"
    assert found.plan == PLAN


def test_a_deleted_anchor_is_absent_not_whatever_took_its_name():
    client = realm()
    request = planned(client)
    client.delete(request.anchor_id)
    assert record.request_at(client, request.anchor_id, BOT_ID) is None


def test_the_run_topic_carries_the_request_id_so_a_stem_can_be_reused():
    assert record.assetrun_topic_name("robot", 5912) == "assetrun-robot-a5912"


# --- the plan and its toolsets ----------------------------------------------


def test_the_plan_and_its_toolsets_come_back_out_of_the_conversation():
    client = realm()
    planned(client, tools=("toolset-image", "toolset-video"))

    found = record.read_request(client, CHANNEL, TOPIC, BOT_ID)

    assert found.plan == PLAN
    assert found.title == "Draw the bird"
    assert found.tools == ["toolset-image", "toolset-video"]
    assert found.state == record.REQUEST_PLANNED


def test_no_tools_note_and_an_empty_one_are_different_answers():
    """`None` is "nobody recorded a selection" and is answered with the whole
    library; `[]` is a recorded selection of none."""
    client = realm()
    request = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).tools is None
    record.record_plan(client, request, PLAN, [])
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).tools == []


def test_planning_again_replaces_the_document_and_keeps_the_history():
    client = realm()
    request = planned(client)
    record.record_plan(client, request, "# Draw two birds\n\nTwo PNGs.", ["toolset-image"])

    found = record.read_request(client, CHANNEL, TOPIC, BOT_ID)

    assert found.plan == "# Draw two birds\n\nTwo PNGs."
    assert PLAN in client.posts(CHANNEL, TOPIC)  # the old plan is still there


def test_a_plan_that_would_be_truncated_is_refused_rather_than_half_recorded():
    """Zulip accepts an over-long post and truncates it silently."""
    client = realm()
    request = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    with pytest.raises(record.RecordError, match="truncates"):
        record.record_plan(client, request, "# Big\n\n" + "x" * record.POST_LIMIT, [])


def test_a_plan_that_is_not_a_document_is_refused():
    client = realm()
    request = record.ensure_request(client, CHANNEL, TOPIC, BOT_ID)
    with pytest.raises(record.RecordError):
        record.record_plan(client, request, "   ", [])


# --- the run, and the relationship ------------------------------------------


def test_opening_a_run_anchors_it_to_the_request_by_id():
    client = realm()
    request = planned(client)

    run = record.open_run(client, request, BOT_ID)

    assert run.topic == f"assetrun-robot-{request.label}"
    assert parse_run(client.message(run.anchor_id)["content"]) == request.anchor_id
    bodies = client.posts(CHANNEL, run.topic)
    assert bodies[0] == f"[selfnote][rootchat] {CHANNEL}/{TOPIC}"
    assert bodies[1] == f"[selfnote][assetrun] {request.anchor_id}"
    assert "selfnote" not in bodies[2] and "Post here to start it" in bodies[2]


def test_opening_a_run_twice_finds_the_one_that_is_there():
    client = realm()
    request = planned(client)
    first = record.open_run(client, request, BOT_ID)
    again = record.open_run(client, request, BOT_ID)
    assert again.anchor_id == first.anchor_id
    assert len(client.posts(CHANNEL, first.topic)) == 3


def test_a_run_follows_its_request_home_by_id_not_by_name():
    client = realm()
    request = planned(client)
    run = record.open_run(client, request, BOT_ID)
    client.rename_topic(request.anchor_id, "✔ retired-assetplan-robot-a1")

    found = record.request_of_run(client, run, BOT_ID)

    assert found.topic == "retired-assetplan-robot-a1"


# --- states and results -----------------------------------------------------


def test_a_fresh_attempt_does_not_inherit_the_old_verdict():
    client = realm()
    request = planned(client)
    request = record.set_request_state(client, request, record.REQUEST_DELIVERED)
    record.set_request_state(client, request, record.REQUEST_FAILED)
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).state == record.REQUEST_FAILED
    record.set_request_state(client, request, record.REQUEST_DELIVERED)
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).state == record.REQUEST_DELIVERED


def test_a_result_is_the_durable_key_in_both_conversations():
    client = realm()
    request = planned(client)
    run = record.open_run(client, request, BOT_ID)

    record.record_result(client, run, request, "files/2026-09-10/bird.zip")

    note = "[selfnote][result] files/2026-09-10/bird.zip"
    assert note in client.posts(CHANNEL, TOPIC)
    assert note in client.posts(CHANNEL, run.topic)
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).results == (
        "files/2026-09-10/bird.zip",)


def test_a_second_attempt_appends_its_key_rather_than_replacing_one():
    client = realm()
    request = planned(client)
    run = record.open_run(client, request, BOT_ID)
    record.record_result(client, run, request, "files/one.zip")
    record.record_result(client, run, request, "files/two.zip")
    assert record.read_request(client, CHANNEL, TOPIC, BOT_ID).results == (
        "files/one.zip", "files/two.zip")


# --- retirement and replacement ---------------------------------------------


def test_retiring_releases_the_stem_and_takes_both_topics_out_of_the_sweep():
    client = realm()
    request = planned(client)
    run = record.open_run(client, request, BOT_ID)

    lines = record.retire_request(client, request, BOT_ID)

    names = [name for _, name in client.histories]
    assert f"✔ retired-{TOPIC}-{request.label}" in names
    assert f"✔ retired-{run.topic}-{run.label}" in names
    assert TOPIC not in names and run.topic not in names
    assert len(lines) == 2
    # The state was written before the rename, so it travelled with the
    # conversation rather than landing in whatever takes the freed name.
    assert "[selfnote][state] retired" in client.posts(
        CHANNEL, f"retired-{TOPIC}-{request.label}")


def test_a_replacement_takes_the_freed_name_and_says_what_it_replaced():
    client = realm()
    first = planned(client)
    record.open_run(client, first, BOT_ID)
    record.retire_request(client, first, BOT_ID)

    client.send_to_channel(CHANNEL, TOPIC, "actually, make it a robot")
    second = record.open_replacement(client, CHANNEL, TOPIC, BOT_ID, first)

    assert second.anchor_id != first.anchor_id
    assert second.replaces == first.anchor_id
    assert second.run_topic != first.run_topic
    # Two requests of the same name, told apart by anchor alone.
    assert record.request_at(client, first.anchor_id, BOT_ID).state == record.REQUEST_RETIRED
    assert record.request_at(client, second.anchor_id, BOT_ID).topic == TOPIC


def test_a_late_result_belongs_to_the_request_that_asked_for_it():
    """The old run topic still names the old request by id, so a callback
    that arrives after the replacement delivers to the retired conversation
    — not to the new request wearing the same stem."""
    client = realm()
    first = planned(client)
    old_run = record.open_run(client, first, BOT_ID)
    record.retire_request(client, first, BOT_ID)
    second = record.open_replacement(client, CHANNEL, TOPIC, BOT_ID, first)
    record.record_plan(client, second, PLAN, ["toolset-image"])
    record.open_run(client, second, BOT_ID)

    late = record.request_of_run(client, old_run, BOT_ID)

    assert late.anchor_id == first.anchor_id
    assert late.topic == f"retired-{TOPIC}-{first.label}"


# --- the retirement command -------------------------------------------------


def test_retire_reports_what_moved_and_can_open_the_replacement():
    from agforge import retire as command

    client = realm()
    first = planned(client)
    record.open_run(client, first, BOT_ID)

    lines = command.retire(client, CHANNEL, TOPIC, replace=True)

    assert any("retired" in line for line in lines)
    fresh = record.read_request(client, CHANNEL, TOPIC, BOT_ID)
    assert fresh.replaces == first.anchor_id
    assert lines[-1].startswith(f"opened {fresh.label} under {TOPIC}")


def test_retire_refuses_a_conversation_that_is_not_a_request():
    from agforge import retire as command

    client = Realm({(CHANNEL, "chit-chat"): [human("hello")]})
    with pytest.raises(record.RecordError, match="not a request of mine"):
        command.retire(client, CHANNEL, "chit-chat")
