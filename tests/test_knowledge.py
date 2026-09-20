"""`agforge knowledge`: the sources a run can search, read and cite.

What is pinned: the config names the sources and nothing else does; a
listing shows every index row whole, unverified ones included; a reference
cannot climb out of its source; search is bounded and says so; the stamp a
plan records comes back out of the conversation and the run's `knowledge.md`
says when a source has moved since.
"""

import subprocess

import pytest

from agforge import assetrun_topic, cli, knowledge, record

from realm import BOT_ID, Realm, human


def checkout(path, index_text, extra=None):
    path.mkdir(parents=True)
    (path / "INDEX.md").write_text(index_text)
    for name, body in (extra or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "init"], check=True)
    return path


def library(tmp_path):
    general = checkout(tmp_path / "main", (
        "# Index\n\n| subject | folder | tips |\n|---|---|---|\n"
        "| Pixel Art | `pixel/` | 2026-08-30 |\n| Flux Kontext | `kontext/` | no |\n"
    ), {"pixel/tips.md": "- CFG 4 keeps the corner radius\n"})
    local = checkout(tmp_path / "localize", (
        "| capability | folder | state |\n|:--|:--|:--|\n"
        "| HUD icons | `hud_icons/` | `planned` |\n"
    ), {"hud_icons/README.md": "# hud_icons\nradius 22 %\n", ".local/env.toml": "secret = 1\n"})
    config = tmp_path / "knowledge.toml"
    config.write_text(
        f'schema = "agforge.knowledge.v1"\n'
        f'[[source]]\nname = "mediagen"\nkind = "general"\npath = "{general}"\nabout = "general"\n'
        f'[[source]]\nname = "localize"\nkind = "local"\npath = "{local}"\n'
    )
    return config


def sources(tmp_path):
    return knowledge.load_sources(library(tmp_path))


# --- the config -------------------------------------------------------------


def test_no_config_is_an_empty_library_not_an_error(tmp_path):
    assert knowledge.load_sources(tmp_path / "missing.toml") == []
    assert "no knowledge sources" in knowledge.listing([])


def test_a_wrong_kind_or_a_nameless_source_is_refused(tmp_path):
    bad = tmp_path / "k.toml"
    bad.write_text('[[source]]\nname = "x"\npath = "/tmp"\nkind = "secret"\n')
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.load_sources(bad)
    bad.write_text('[[source]]\npath = "/tmp"\n')
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.load_sources(bad)


# --- the listing --------------------------------------------------------------


def test_the_listing_shows_every_source_its_revision_and_every_index_row(tmp_path):
    found = sources(tmp_path)
    text = knowledge.listing(found)
    for source in found:
        assert f"## {source.name} ({source.kind}) @{knowledge.revision(source)}" in text
        assert knowledge.revision(source) != knowledge.NO_REVISION
    # Unverified rows are shown with their state, never hidden.
    assert "| Flux Kontext | `kontext/` | no |" in text
    assert "| HUD icons | `hud_icons/` | `planned` |" in text
    assert "|---|" not in text and "|:--|" not in text


def test_a_source_that_is_not_checked_out_is_said_not_skipped(tmp_path):
    missing = knowledge.Source("gone", tmp_path / "nowhere", "local")
    text = knowledge.listing([missing])
    assert "## gone (local) @-" in text and "not checked out" in text


# --- one file ------------------------------------------------------------------


def test_show_reads_a_file_or_lists_a_directory(tmp_path):
    found = sources(tmp_path)
    assert knowledge.show("mediagen/pixel/tips.md", found) == "- CFG 4 keeps the corner radius\n"
    assert knowledge.show("localize/hud_icons", found) == "README.md"
    # `.local/` is not listed: it is the host's, not the knowledge.
    assert ".local" not in knowledge.show("localize", found)


def test_a_reference_cannot_climb_out_of_its_source(tmp_path):
    found = sources(tmp_path)
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.show("localize/../main/INDEX.md", found)
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.show("elsewhere/INDEX.md", found)
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.show("localize/hud_icons/none.md", found)


def test_path_is_where_a_script_can_be_run_from(tmp_path):
    found = sources(tmp_path)
    assert knowledge.path_of("localize/hud_icons", found).endswith("localize/hud_icons")


# --- search ------------------------------------------------------------------


def test_search_needs_every_term_on_one_line_and_names_the_place(tmp_path):
    found = sources(tmp_path)
    assert knowledge.search(["corner", "RADIUS"], found) == [
        "mediagen/pixel/tips.md:1: - CFG 4 keeps the corner radius",
    ]
    assert knowledge.search(["radius", "planned"], found) == []
    assert knowledge.search([], found) == []


def test_search_skips_the_ignored_host_files(tmp_path):
    assert knowledge.search(["secret"], sources(tmp_path)) == []


def test_search_is_bounded_and_says_so(tmp_path):
    found = sources(tmp_path)
    hits = knowledge.search(["e"], found, limit=2)
    assert len(hits) == 3 and hits[-1].startswith("… stopped at 2 hits")


# --- the CLI -----------------------------------------------------------------


def test_the_cli_reaches_all_four_verbs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(knowledge, "CONFIG_PATH", library(tmp_path))
    cli.main(["knowledge", "list"])
    assert "## localize (local)" in capsys.readouterr().out
    cli.main(["knowledge", "show", "localize/hud_icons/README.md"])
    assert "radius 22 %" in capsys.readouterr().out
    cli.main(["knowledge", "search", "radius"])
    assert "localize/hud_icons/README.md:2:" in capsys.readouterr().out
    cli.main(["knowledge", "path", "mediagen"])
    assert capsys.readouterr().out.strip().endswith("main")
    with pytest.raises(SystemExit):
        cli.main(["knowledge", "show", "mediagen/../x"])


# --- the stamp, recorded and read back -----------------------------------------


def test_the_stamp_names_each_source_at_its_revision(tmp_path):
    found = sources(tmp_path)
    stamp = knowledge.stamp(found)
    parsed = knowledge.parse_stamp(stamp)
    assert set(parsed) == {"mediagen", "localize"}
    assert all(len(rev) >= 7 for rev in parsed.values())
    assert knowledge.parse_stamp("") == {} and knowledge.parse_stamp(None) == {}


def test_a_plan_records_what_it_was_made_against_and_it_comes_back():
    client = Realm({("c", "assetplan-x"): [human("icons please")]})
    request = record.ensure_request(client, "c", "assetplan-x", BOT_ID)
    assert record.read_request(client, "c", "assetplan-x", BOT_ID).knowledge is None
    record.record_plan(client, request, "# Icons\n\nFive.", ["toolset-image"],
                       "mediagen@c415b0c, localize@24caf95")
    found = record.read_request(client, "c", "assetplan-x", BOT_ID)
    assert found.knowledge == "mediagen@c415b0c, localize@24caf95"
    assert "[selfnote][knowledge] mediagen@c415b0c, localize@24caf95" in client.posts("c", "assetplan-x")


def test_a_plan_made_with_no_sources_records_none_not_nothing():
    client = Realm({("c", "assetplan-y"): [human("hi")]})
    request = record.ensure_request(client, "c", "assetplan-y", BOT_ID)
    record.record_plan(client, request, "# P\n\nbody", [], "")
    assert record.read_request(client, "c", "assetplan-y", BOT_ID).knowledge == ""


def test_the_run_workspace_says_whether_the_knowledge_moved():
    same = assetrun_topic.knowledge_summary("mediagen@aaa, localize@bbb", "mediagen@aaa, localize@bbb")
    assert "Planned against: mediagen@aaa, localize@bbb" in same and "Moved" not in same
    moved = assetrun_topic.knowledge_summary("mediagen@aaa, localize@bbb", "mediagen@aaa, localize@ccc")
    assert "Moved since the plan: localize bbb -> ccc" in moved
    assert "predates" in assetrun_topic.knowledge_summary(None, "mediagen@aaa")
    assert "No knowledge source was configured" in assetrun_topic.knowledge_summary("", "")


# --- the hand-over through the two topic flows ----------------------------------


def test_the_plan_registration_says_what_it_was_made_against(monkeypatch, tmp_path):
    """The requester sees the stamp in the registration line, and the run
    topic's workspace gets `knowledge.md` built from the same record."""
    import test_assetplan_topic as plan_flow
    import test_assetrun_topic as run_flow

    calls = []
    plan_flow.wire(
        monkeypatch, tmp_path, calls, writes_required=True,
        toolsets_csv="toolset-image\n", writes=(("plan.md", "# Icons\n\nFive."),),
    )
    monkeypatch.setattr(knowledge, "CONFIG_PATH", library(tmp_path))
    client = plan_flow.Client(calls)
    from agforge import assetplan_topic
    assetplan_topic.handle_topic(client, plan_flow.CHANNEL, plan_flow.TOPIC)
    request = record.read_request(client, plan_flow.CHANNEL, plan_flow.TOPIC, BOT_ID)
    assert knowledge.parse_stamp(request.knowledge).keys() == {"mediagen", "localize"}
    assert f"knowledge: {request.knowledge}" in plan_flow.written(calls)[-1]

    summary = assetrun_topic.knowledge_summary(request.knowledge)
    assert f"Planned against: {request.knowledge}" in summary


def test_the_run_workspace_carries_the_stamp_the_plan_recorded(monkeypatch, tmp_path):
    import test_assetrun_topic as run_flow

    calls = []
    run_flow.wire(monkeypatch, tmp_path, calls)
    client = run_flow.Client(calls)
    assetrun_topic.handle_assetrun(client, run_flow.CHANNEL, client.topic)
    text = (run_flow.ws(tmp_path, client) / assetrun_topic.KNOWLEDGE_FILE).read_text()
    assert text.startswith("# Knowledge this plan was made against")
    assert "agforge knowledge list|show|search|path" in text
