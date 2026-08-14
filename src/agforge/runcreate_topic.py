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

from . import generate
from .role_run import AGFORGE_ROOT, run_role
from .works import Work, next_work, report_work
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
    "deliver_to_origin",
    "handle_runcreate",
    "prepare_workspace",
    "result_files",
    "run_generator",
    "upload_result",
    "workspace_dir",
    "zip_result",
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
        # "Success" is the run exiting zero — `run_generator` raised
        # otherwise. An empty `result/` is a legitimate pure-text outcome,
        # not a failure signal.

        step = "packaging the result"
        files = result_files(workspace)
        if files:
            delivery = (
                f"result of \"{chosen.name}\" ({len(files)} file(s)), "
                f"temporary download (expires in {generate.DEFAULT_TTL_MINUTES} min): "
                f"{upload_result(zip_result(workspace))}"
            )
            sections.append(f"result/ holds {len(files)} file(s); zipped and uploaded")
        else:
            delivery = answer
            sections.append("result/ is empty; delivering the answer text")

        step = "origin delivery"
        sections.append(deliver_to_origin(client, chosen, delivery))

        step = "reporting to plane"
        label, commented, completed = report_work(
            chosen.project_id, chosen.issue_id, answer, True
        )
        sections.append(
            f"work {label}: commented {'yes' if commented else 'no'}, "
            f"Done {'yes' if completed else 'no'}"
        )
    except Exception as error:  # noqa: BLE001 - the topic is the error channel
        log(f"runcreate topic workflow failed during {step}: {error!r}")
        sections.append(f"failed during {step}: {error}")

    topic_write(topic, "\n\n".join(section for section in sections if section),
                channel=channel, client=client)


def result_files(workspace: Path) -> list[Path]:
    """Every file under `result/`, in stable order."""
    return sorted(path for path in (workspace / "result").rglob("*") if path.is_file())


def zip_result(workspace: Path) -> Path:
    """`result/` as `result.zip` in the workspace root — outside the archived
    directory, so it can never contain itself. Overwritten on re-trigger."""
    return Path(shutil.make_archive(
        str(workspace / "result"), "zip", root_dir=workspace / "result"
    ))


def upload_result(archive: Path) -> str:
    """One presigned download URL for the archive.

    `generate.load_env`/`upload_and_presign` answer a missing configuration
    with `sys.exit`, which is right for the CLI they serve and wrong here —
    a SystemExit would sail past the handler's error discipline.
    """
    try:
        return generate.upload_and_presign(
            generate.load_env(), archive, generate.DEFAULT_TTL_MINUTES
        )
    except SystemExit as error:
        raise ListenerError(f"upload failed: {error}") from error


def deliver_to_origin(client: ZulipClient, work: Work, delivery: str) -> str:
    """Post the delivery to the topic the request came from, and say what
    happened either way — the runcreate summary must survive everything,
    including a dead origin channel.

    The origin `create-` topic may already be resolved (`✔`); posting under
    the plain name still lands (Zulip treats it as that topic's thread), and
    this bot being last poster there cannot re-trigger the create sweep.
    """
    origin = work.origin()
    if origin is None:
        return f"no origin topic recorded; the result stays here:\n\n{delivery}"
    channel, topic = origin
    try:
        topic_write(topic, delivery, channel=channel, client=client)
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        log(f"origin delivery to {channel!r}/{topic!r} failed: {error!r}")
        return (
            f"could not deliver to {channel}/{topic} ({error}); "
            f"the result stays here:\n\n{delivery}"
        )
    return f"delivered to {channel}/{topic}"
