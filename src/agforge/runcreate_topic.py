"""Execute one Work, triggered by any non-bot post in a `runcreate-` topic.

autolab's `handle_run` discipline, on agforge's vocabulary: a `runcreate-`
topic is a button, not a conversation. The chatlog is never read — whatever
the topic gets, one eligible Work is chosen from Plane (`works.next_work`),
executed by the generator in the Work's own persistent workspace, and the
result is delivered back to the topic the request originally came from.

The workspace is `.local/agentws/<work id>/generator/` — per Work, not per
topic, and never deleted. A re-trigger rebuilds `plan.md` and `tools/` from
the Work and leaves `result/`/`intermediate/` as they are; there is no dirty
check on purpose (the braindump drops autolab's create/delete dance).

`tools/` is what the Work's `[TOOLS]` description footer names — the
toolsets the create flow planned it with. A Work without that footer is
hand-made, or predates this phase, and gets the whole library.

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

from . import generate, toolsets
from .plane import split_tools_footer
from .role_run import AGFORGE_ROOT, run_role
from .works import Work, next_work, report_work
from .zulip_chat import SWEEP_ACK

AGENTWS_ROOT = AGFORGE_ROOT / ".local" / "agentws"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

TOOLS_DIR = "tools"

# The generator's own verdict on its run, from `runcreate_generator/guide.md`.
# The exit code stays the first-class failure signal; this is the agent
# saying so itself when the harness saw nothing wrong.
FAILURE_FLAG = "failure.flag"

# Real work, not a planning pass: autolab's work run uses 1200 s and the
# create-flow generator 900 s; this sits at the top of that range.
RUNCREATE_TIMEOUT_SECONDS = 1200

NO_WORK_REPLY = "no work"
FAILED_PREFIX = "the run reported failure; what it produced follows"

# The durable half of a delivery, on its own last line — the same shape as the
# `[TOOLS]` footer `plane.py` already puts in a description. A presigned URL
# dies after `generate.DEFAULT_TTL_MINUTES`; the object behind it does not, so
# whoever reads this later re-signs the key through `POST /api/resign` instead
# of finding an expired link. Carried by both the delivery post and the Plane
# comment, because a consumer may only be looking at one of them.
S3_KEY_MARKER = "[S3KEY]"

__all__ = [
    "AGENTWS_ROOT",
    "FAILED_PREFIX",
    "FAILURE_FLAG",
    "NO_WORK_REPLY",
    "S3_KEY_MARKER",
    "ListenerError",
    "deliver_to_origin",
    "handle_runcreate",
    "prepare_workspace",
    "result_files",
    "run_generator",
    "s3_key_footer",
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
    split it from, minus the `[TOOLS]` footer — that line is addressed to
    this function, not to the generator. `tools/` is rebuilt from it, both
    derived from the Work and both replaced on a re-trigger; `result/` and
    `intermediate/` are left as they are.

    A leftover `failure.flag` is removed here, so a re-trigger starts clean
    and the flag found after the run is this run's own verdict.
    """
    workspace = workspace_dir(work.issue_id)
    workspace.mkdir(parents=True, exist_ok=True)
    description, requested = split_tools_footer(work.description)
    (workspace / "plan.md").write_text(
        compose_document(work.name, description_html(description)), encoding="utf-8"
    )
    shutil.rmtree(workspace / TOOLS_DIR, ignore_errors=True)
    toolsets.place(
        toolsets.names() if requested is None else requested, workspace / TOOLS_DIR
    )
    (workspace / FAILURE_FLAG).unlink(missing_ok=True)
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
        # The run exiting zero is not the whole verdict: the guide tells the
        # generator to leave `failure.flag` when it knows it failed. An empty
        # `result/` is still a legitimate pure-text outcome, not a signal.
        succeeded = not (workspace / FAILURE_FLAG).exists()
        if not succeeded:
            sections.append(f"{FAILURE_FLAG} is present: the generator reports failure")

        step = "packaging the result"
        files = result_files(workspace)
        comment = answer
        if files:
            key, url = upload_result(zip_result(workspace))
            footer = s3_key_footer(key)
            delivery = (
                f"result of \"{chosen.name}\" ({len(files)} file(s)), "
                f"temporary download (expires in {generate.DEFAULT_TTL_MINUTES} min): "
                f"{url}\n{footer}"
            )
            # The Plane comment is the ledger consumers read months later, so
            # it carries the key too — never only the URL that outlives it by
            # an hour.
            comment = f"{answer}\n\n{footer}" if answer else footer
            sections.append(
                f"result/ holds {len(files)} file(s); zipped and uploaded as {key}"
            )
        else:
            delivery = answer
            sections.append("result/ is empty; delivering the answer text")
        if not succeeded:
            # The requester hears the same verdict the Work does; whatever
            # the run did produce still travels with it.
            delivery = f"{FAILED_PREFIX}\n\n{delivery}"

        step = "origin delivery"
        sections.append(deliver_to_origin(client, chosen, delivery))

        step = "reporting to plane"
        # `success=False` leaves the Work in its unstarted state, so it stays
        # selectable and a re-trigger runs it again.
        label, commented, completed = report_work(
            chosen.project_id, chosen.issue_id, comment, succeeded
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


def upload_result(archive: Path) -> tuple[str, str]:
    """`(bucket key, presigned download URL)` for the archive.

    `generate.load_env`/`upload_and_presign_key` answer a missing
    configuration with `sys.exit`, which is right for the CLI they serve and
    wrong here — a SystemExit would sail past the handler's error discipline.
    """
    try:
        return generate.upload_and_presign_key(
            generate.load_env(), archive, generate.DEFAULT_TTL_MINUTES
        )
    except SystemExit as error:
        raise ListenerError(f"upload failed: {error}") from error


def s3_key_footer(key: str) -> str:
    return f"{S3_KEY_MARKER} {key}"


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
