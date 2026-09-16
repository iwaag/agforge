"""forge in an argue (`argue` p1): a contribution when named, no asset.

forge had no mention route: its requests are its own `assetplan-`/`assetrun-`
topics, and a mention elsewhere meant nothing to it. An argue invitation
(`agag.argue`) is the one mention it now answers, in the argue topic, from
what it can make and how; every other mention stays logged and left, and
nothing here plans or generates an asset.
"""

from __future__ import annotations

from pathlib import Path

from agag.argue import ROLE as ARGUE_ROLE, is_argue_topic, participate, role_context_path
from agag.topics import next_record_path
from agag.zulip import ZulipClient, log

from .instance import AGFORGE_ROOT, SPEC
from .role_run import run_role

ARGUE_TIMEOUT_SECONDS = 600

__all__ = ["ARGUE_ROLE", "ARGUE_TIMEOUT_SECONDS", "handle_mention", "role_context", "run_argue"]


def role_context() -> str:
    return role_context_path(AGFORGE_ROOT, ARGUE_ROLE).read_text(encoding="utf-8")


def run_argue(prompt: str, cwd: Path, invitation) -> str:
    output, _, exit_code = run_role(
        ARGUE_ROLE, prompt, cwd=cwd, timeout=ARGUE_TIMEOUT_SECONDS,
        record=next_record_path(SPEC.records_root / ARGUE_ROLE), transcript=cwd / "transcript.jsonl",
        stream=True,
    )
    if exit_code != 0:
        raise RuntimeError(f"{ARGUE_ROLE} run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def handle_mention(client: ZulipClient, channel: str, topic: str) -> None:
    if not is_argue_topic(channel, topic):
        log(f"mention in {channel!r}/{topic!r} is not an argue; ignoring")
        return
    from agag.agent import is_ack

    log(f"argue invitation in {channel!r}/{topic!r}")
    participate(client, channel, topic, spec=SPEC, role_context=role_context(), run=run_argue,
                drop=is_ack, log=log)
