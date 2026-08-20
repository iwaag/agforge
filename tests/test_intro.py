from datetime import date

from agforge import intro


def test_intro_text_is_fixed_markdown_with_a_freshness_stamp(monkeypatch, tmp_path):
    source = tmp_path / "intro.md"
    source.write_text("# agforge\n\nOpen a `create-…` topic.\n", encoding="utf-8")
    monkeypatch.setattr(intro, "INTRO_PATH", source)

    assert intro.intro_text(date(2026, 8, 20), "3939f26") == (
        "# agforge\n\nOpen a `create-…` topic.\n\n---\n"
        "Posted: 2026-08-20\nRevision: `3939f26`\n"
    )


def test_main_posts_to_the_shared_agents_intro_topic(monkeypatch):
    sent = []

    class Client:
        def send_to_channel(self, channel, topic, text):
            sent.append((channel, topic, text))

    monkeypatch.setattr(intro.ZulipClient, "from_env", lambda path: Client())
    monkeypatch.setattr(intro, "instance_name", lambda: "agforge-agstudio1")
    monkeypatch.setattr(intro, "intro_text", lambda: "intro body\n")

    intro.main()

    assert sent == [("agents", "intro-agforge-agstudio1", "intro body\n")]

