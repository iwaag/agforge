"""Execute one Work, triggered by any non-bot post in a `runcreate-` topic.

autolab's `handle_run` discipline, on agforge's vocabulary: a `runcreate-`
topic is a button, not a conversation. The chatlog is never read — whatever
the topic gets, one eligible Work is chosen from Plane (`works.next_work`),
executed by the generator in the Work's own persistent workspace, and the
result is delivered back to the topic the request originally came from.

The workspace is `.local/agentws/<work id>/generator/` — per Work, not per
topic, and never deleted. A re-trigger overwrites `plan.md`/`tools.md` in
place and leaves `result/`/`intermediate/` as they are; there is no dirty
check on purpose (the braindump drops autolab's create/delete dance).

After the ack, every path posts to the topic before returning: the sweep only
re-fires when the last poster is not the bot, so the final post is both the
report and the off-switch.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agag.plane import compose_document, description_html
from agag.topics import guide as shared_guide, next_record_path
from agag.zulip import ZulipClient, log, topic_write

from .role_run import AGFORGE_ROOT, run_role
from .works import Work, next_work
from .zulip_chat import SWEEP_ACK

AGENTWS_ROOT = AGFORGE_ROOT / ".local" / "agentws"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

# The same file the create flow copies: the generator's tool vocabulary is
# one document, wherever the run happens.
TOOLS_FILE = GUIDES / "create_generator" / "tools.md"

# Real work, not a planning pass: autolab's work run uses 1200 s and the
# create-flow generator 900 s; this sits at the top of that range.
RUNCREATE_TIMEOUT_SECONDS = 1200

NO_WORK_REPLY = "no work"

__all__ = [
    "AGENTWS_ROOT",
    "NO_WORK_REPLY",
    "ListenerError",
    "handle_runcreate",
    "prepare_workspace",
    "run_generator",
    "workspace_dir",
]


class ListenerError(RuntimeError):
    """One runcreate-topic workflow could not complete."""


def workspace_dir(issue_id: str) -> Path:
    """`.local/agentws/<work id>/generator/` — the Work's own directory."""
    return AGENTWS_ROOT / issue_id / "generator"


def prepare_workspace(work: Work) -> Path:
    """Build (or refresh) the Work's workspace.

    `plan.md` is the Work itself, in the same document shape `register_plan`
    split it from; `tools.md` is the create flow's copy. Both are overwritten
    on a re-trigger; `result/` and `intermediate/` are left as they are.
    """
    workspace = workspace_dir(work.issue_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "plan.md").write_text(
        compose_document(work.name, description_html(work.description)), encoding="utf-8"
    )
    shutil.copyfile(TOOLS_FILE, workspace / "tools.md")
    (workspace / "result").mkdir(exist_ok=True)
    (workspace / "intermediate").mkdir(exist_ok=True)
    return workspace


def run_generator(workspace: Path) -> str:
    """One generator run in the Work's workspace, with its record."""
    record = next_record_path(RECORDS_ROOT / "runcreate")
    output, _, exit_code = run_role(
        "generator",
        shared_guide(GUIDES, "runcreate_generator", "guide.md"),
        cwd=workspace,
        timeout=RUNCREATE_TIMEOUT_SECONDS,
        record=record,
    )
    if exit_code != 0:
        raise ListenerError(f"generator run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def handle_runcreate(client: ZulipClient, channel: str, topic: str) -> None:
    """Choose, execute, deliver, and always answer the topic."""
    log(f"runcreate topic {channel!r}/{topic!r}")
    topic_write(topic, SWEEP_ACK, channel=channel, client=client)

    sections: list[str] = []
    step = "choosing the work"
    try:
        chosen = next_work()
        if chosen is None:
            topic_write(topic, NO_WORK_REPLY, channel=channel, client=client)
            return
        sections.append(f'running "{chosen.name}"')

        step = "preparing the workspace"
        workspace = prepare_workspace(chosen)

        step = "generator run"
        answer = run_generator(workspace)

        step = "delivering the result"
        sections.extend(deliver_result(client, chosen, workspace, answer))
    except Exception as error:  # noqa: BLE001 - the topic is the error channel
        log(f"runcreate topic workflow failed during {step}: {error!r}")
        sections.append(f"failed during {step}: {error}")

    topic_write(topic, "\n\n".join(section for section in sections if section),
                channel=channel, client=client)


def deliver_result(
    client: ZulipClient, work: Work, workspace: Path, answer: str
) -> list[str]:
    """Deliver the run's outcome (Step 4). For now: the answer stays in the
    summary; origin delivery and the Plane write-back arrive next step."""
    return [answer]
