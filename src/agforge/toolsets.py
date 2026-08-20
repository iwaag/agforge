"""The toolset documents in `agent/toolsets/`, and how they reach a workspace.

One toolset is one Markdown file, `toolset-<name>.md`, opening with a
`# Description` section. Three places read them and none of them should have
its own spelling of the rules:

- `agforge toolsets --list` — what the front sees.
- the create flow — the front's `toolsets.csv` becomes `generator/tools/`.
- the assetrun flow — a Work's `[TOOLS]` footer becomes the same `tools/`.

Name resolution is deliberately lenient. The name may arrive with or without
its `.md` extension and with the `--list` description tail still attached,
because it was copied by an agent from a printed line. An unresolvable name
is skipped with a log line, never raised: a run that can do most of the job
is worth more than a run that refuses over one bad word.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from agag.zulip import log

from .role_run import AGFORGE_ROOT

TOOLSETS_DIR = AGFORGE_ROOT / "agent" / "toolsets"
TOOLSET_PREFIX = "toolset-"
TOOLSET_GLOB = f"{TOOLSET_PREFIX}*.md"

DESCRIPTION_HEADING = re.compile(r"^#\s+Description\s*$", re.IGNORECASE)
ANY_HEADING = re.compile(r"^#{1,6}\s")

__all__ = [
    "TOOLSETS_DIR",
    "describe",
    "listing",
    "names",
    "parse_names",
    "place",
    "resolve",
]


def describe(text: str) -> str:
    """The body of a toolset's leading `# Description` section, on one line.

    A file without that heading still describes nothing rather than failing:
    the front should see every toolset that exists, malformed ones included.
    """
    body: list[str] = []
    collecting = False
    for line in text.splitlines():
        if DESCRIPTION_HEADING.match(line):
            collecting = True
            continue
        if collecting and ANY_HEADING.match(line):
            break
        if collecting:
            body.append(line)
    return " ".join(" ".join(body).split())


def paths(directory: Path | None = None) -> list[Path]:
    return sorted((TOOLSETS_DIR if directory is None else directory).glob(TOOLSET_GLOB))


def names(directory: Path | None = None) -> list[str]:
    """Every toolset name, extension-less, in listing order."""
    return [path.stem for path in paths(directory)]


def listing(directory: Path | None = None) -> list[str]:
    """One `name, description` line per toolset.

    This is what `toolsets --list` prints and what the front copies into
    `toolsets.csv`, so it is a contract: the first comma-separated field is
    the toolset name.
    """
    lines = []
    for path in paths(directory):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        lines.append(f"{path.stem}, {describe(text)}".rstrip(", "))
    return lines


def resolve(name: str, directory: Path | None = None) -> Path | None:
    """The file for one written-down toolset name, or None.

    Accepts `toolset-image`, `toolset-image.md`, a whole `--list` line, and
    any of those in a different case.
    """
    candidate = str(name).split(",")[0].strip().strip("\"'").strip()
    if not candidate:
        return None
    candidate = Path(candidate).name
    if candidate.lower().endswith(".md"):
        candidate = candidate[: -len(".md")]
    root = TOOLSETS_DIR if directory is None else directory
    for path in paths(root):
        if path.stem.lower() == candidate.lower():
            return path
    return None


def parse_names(text: str) -> list[str]:
    """The toolset names in a `toolsets.csv` body, in order, without repeats.

    Blank lines and `#` comments are ignored; a header row naming no toolset
    resolves to nothing later and costs only a log line.
    """
    seen: list[str] = []
    for line in text.splitlines():
        name = line.split(",")[0].strip().strip("\"'").strip()
        if not name or name.startswith("#") or name in seen:
            continue
        seen.append(name)
    return seen


def place(requested, target: Path, directory: Path | None = None) -> list[str]:
    """Copy each named toolset into `target/`, answering what actually landed.

    `target` is created either way: an empty `tools/` is a legitimate
    outcome the guides already route (the generator asks back, writes
    `idea.md`, or declines), and its absence would be a different, murkier
    signal.
    """
    target.mkdir(parents=True, exist_ok=True)
    placed: list[str] = []
    for name in requested:
        source = resolve(name, directory)
        if source is None:
            log(f"unknown toolset {str(name).strip()!r}; skipped")
            continue
        shutil.copyfile(source, target / source.name)
        placed.append(source.stem)
    return placed
