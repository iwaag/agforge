"""A Zulip realm small enough to hold a whole forge request, in memory.

`refactor` p2 moves forge's record into the conversations, so the fixtures
have to be conversations: a post has an id, a topic can be renamed and
resolved, and a message id keeps answering with the topic it is in **now**.
Anything less would let a test pass on a record that only works when nothing
is ever renamed — which is precisely the property the phase is about.
"""

from __future__ import annotations

BOT_ID = 13
BOT_NAME = "Forge"
HUMAN_ID = 8
HUMAN_NAME = "Developer"

RESOLVED = "✔ "


class Realm:
    """Channels, topics, posts, and the four operations forge makes of them."""

    def __init__(self, histories=None, streams=None):
        self.histories: dict[tuple[str, str], list[dict]] = dict(histories or {})
        self.streams = dict(streams or {})
        self.next_id = 100
        self.calls: list[tuple] = []
        self.refuse: set[str] = set()

    email = "forge-bot@example.invalid"

    # -- reads ---------------------------------------------------------------

    def whoami(self):
        return {"user_id": BOT_ID, "full_name": BOT_NAME}

    def topic_history(self, channel, topic, num_before=50):
        self.calls.append(("history", channel, topic))
        return list(self.histories.get((channel, topic), []))[-num_before:]

    def message(self, message_id):
        for (channel, topic), posts in self.histories.items():
            for post in posts:
                if int(post["id"]) == int(message_id):
                    return {**post, "display_recipient": channel, "subject": topic}
        return None

    def stream_id(self, name):
        return self.streams.setdefault(name, 100 + len(self.streams))

    def channel_topics(self, stream_id):
        names = {ident: name for name, ident in self.streams.items()}
        channel = names.get(stream_id)
        return [topic for (one, topic) in self.histories if one == channel]

    # -- writes --------------------------------------------------------------

    def send_to_channel(self, channel, topic, content):
        if channel in self.refuse or topic in self.refuse:
            raise RuntimeError(f"the realm refused a post to {channel}/{topic}")
        self.next_id += 1
        self.stream_id(channel)
        post = {"id": self.next_id, "type": "stream", "sender_id": BOT_ID,
                "sender_full_name": BOT_NAME, "display_recipient": channel,
                "subject": topic, "content": content}
        self.histories.setdefault((channel, topic), []).append(post)
        self.calls.append(("post", channel, topic, content))
        return self.next_id

    def rename_topic(self, message_id, new_name):
        """Move every post of a topic, the way `change_all` does."""
        where = self.message(message_id)
        if where is None:
            raise RuntimeError(f"no message {message_id} to rename")
        key = (where["display_recipient"], where["subject"])
        posts = self.histories.pop(key)
        for post in posts:
            post["subject"] = new_name
        self.histories.setdefault((key[0], new_name), []).extend(posts)
        self.calls.append(("rename", key[0], key[1], new_name))

    def resolve_topic(self, message_id, topic):
        if topic.startswith(RESOLVED):
            return
        self.rename_topic(message_id, f"{RESOLVED}{topic}")

    # -- what a test says about it -------------------------------------------

    def posts(self, channel, topic):
        """Every body in a topic, under whichever name it now wears."""
        for (one, name), history in self.histories.items():
            if one == channel and name.removeprefix(RESOLVED) == topic:
                return [post["content"] for post in history]
        return []

    def topic_of(self, message_id):
        found = self.message(message_id)
        return None if found is None else found["subject"]

    def delete(self, message_id):
        """Delete one post — how an anchor becomes *absent* rather than moved."""
        for key, posts in self.histories.items():
            for index, post in enumerate(posts):
                if int(post["id"]) == int(message_id):
                    del posts[index]
                    return


def human(content, id=1, channel="agforge-agstudio1", topic="assetplan-robot",
          sender_id=HUMAN_ID, name=HUMAN_NAME):
    return {"id": id, "type": "stream", "sender_id": sender_id,
            "sender_full_name": name, "display_recipient": channel,
            "subject": topic, "content": content}
