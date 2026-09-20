"""The knowledge sources a run can search, read and cite — `agforge knowledge`.

A toolset says what a run *can run*; knowledge says what is *known* about
making media: mediagen's general, publish-ready study (`main/`), and the
localised form of it that actually runs on this host (`localize/`). Neither
lives in this repository. `.local/knowledge.toml` names where each one is
checked out (`knowledge.example.toml` is the committed shape), and this
module is the one reader.

Four verbs, all reached through the `agforge` CLI so that a role whose grant
is `Bash(agforge:*)` can get at every file without a Read grant on paths
outside its workspace, on every harness alike:

    agforge knowledge list              every source, its revision, its index rows
    agforge knowledge show <src>/<path> one file (or a directory listing)
    agforge knowledge search <terms…>   lines matching every term, across sources
    agforge knowledge path <src>[/<p>]  the absolute path, for running a script

`list` is deliberately the whole index of every source, unfiltered: the plan
decides what is relevant, not this module, and an unverified row is shown
with its state rather than hidden. `stamp()` is what a plan records —
`mediagen@c415b0c, localize@24caf95` — so a run that executes it later can
say what it was planned against and whether that has moved since.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .role_run import AGFORGE_ROOT

CONFIG_PATH = AGFORGE_ROOT / ".local" / "knowledge.toml"
EXAMPLE_CONFIG = AGFORGE_ROOT / "knowledge.example.toml"
SCHEMA = "agforge.knowledge.v1"
KINDS = ("general", "local")
NO_REVISION = "-"
INDEX_NAME = "INDEX.md"
SEARCH_SUFFIXES = (".md", ".py", ".toml", ".txt", ".json")
SKIPPED_DIRS = (".git", ".local", ".venv", "__pycache__", "node_modules")
SEARCH_LIMIT = 200
SHOW_LIMIT = 60_000

__all__ = [
    "CONFIG_PATH",
    "KINDS",
    "NO_REVISION",
    "Source",
    "index_rows",
    "listing",
    "load_sources",
    "parse_stamp",
    "path_of",
    "resolve",
    "revision",
    "search",
    "show",
    "stamp",
]


class KnowledgeError(RuntimeError):
    """A reference that names nothing, or a config that cannot be read."""


@dataclass(frozen=True)
class Source:
    """One checkout of knowledge: where it is and what kind it is."""

    name: str
    path: Path
    kind: str = "general"
    about: str = ""


# --- the config -------------------------------------------------------------


def load_sources(config: Path | None = None) -> list[Source]:
    """The sources `.local/knowledge.toml` names, in file order.

    No file at all is an empty library, not an error: a fresh checkout has
    nothing localised yet and `list` should say so rather than crash. A file
    that is there but wrong is an error, because a run reading an empty
    listing would conclude nothing is known.
    """
    path = CONFIG_PATH if config is None else config
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise KnowledgeError(f"{path}: {error}") from error
    sources: list[Source] = []
    for entry in data.get("source", []) or []:
        name = str(entry.get("name", "")).strip()
        location = str(entry.get("path", "")).strip()
        kind = str(entry.get("kind", "general")).strip() or "general"
        if not name or not location:
            raise KnowledgeError(f"{path}: every [[source]] needs a name and a path")
        if kind not in KINDS:
            raise KnowledgeError(f"{path}: source {name!r} has kind {kind!r}; one of {KINDS}")
        sources.append(Source(
            name=name, path=Path(location).expanduser(), kind=kind,
            about=str(entry.get("about", "")).strip(),
        ))
    return sources


# --- what a source is at ----------------------------------------------------


def revision(source: Source) -> str:
    """The short git revision of a source, or `-` when it is not a checkout."""
    if not source.path.is_dir():
        return NO_REVISION
    try:
        done = subprocess.run(
            ["git", "-C", str(source.path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return NO_REVISION
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else NO_REVISION


def stamp(sources: list[Source] | None = None) -> str:
    """`mediagen@c415b0c, localize@24caf95` — what a plan was made against."""
    found = load_sources() if sources is None else sources
    return ", ".join(f"{source.name}@{revision(source)}" for source in found)


def parse_stamp(text: str | None) -> dict[str, str]:
    """A stamp back into `{name: revision}`; anything else is empty."""
    found: dict[str, str] = {}
    for part in str(text or "").split(","):
        name, _, rev = part.strip().partition("@")
        if name and rev:
            found[name] = rev
    return found


# --- the listing --------------------------------------------------------------


def _index_files(source: Source) -> list[Path]:
    """`INDEX.md` at the root and one level down, in that order."""
    if not source.path.is_dir():
        return []
    found = []
    root = source.path / INDEX_NAME
    if root.is_file():
        found.append(root)
    for child in sorted(source.path.iterdir()):
        if child.name in SKIPPED_DIRS or not child.is_dir():
            continue
        nested = child / INDEX_NAME
        if nested.is_file():
            found.append(nested)
    return found


def index_rows(source: Source) -> list[tuple[str, list[str]]]:
    """Per index file, its table rows verbatim (header included, rule dropped).

    The rows are handed over as written: which column says a subject is
    verified is the index's own business, and a reader that is a model
    reads a table better than any re-flowing this module could do.
    """
    result = []
    for index in _index_files(source):
        rows = []
        for line in index.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(set(cell) <= set("-: ") for cell in cells):
                continue  # the |---|---| rule
            rows.append(stripped)
        result.append((str(index.relative_to(source.path)), rows))
    return result


def listing(sources: list[Source] | None = None) -> str:
    """What `agforge knowledge list` prints: every source, whole."""
    found = load_sources() if sources is None else sources
    if not found:
        return (
            "no knowledge sources are configured on this host "
            f"({CONFIG_PATH.name} is missing); only the toolsets apply"
        )
    blocks = []
    for source in found:
        head = f"## {source.name} ({source.kind}) @{revision(source)}"
        lines = [head]
        if source.about:
            lines.append(source.about)
        if not source.path.is_dir():
            lines.append("(not checked out on this host)")
        for index, rows in index_rows(source):
            lines.append(f"### {source.name}/{index}")
            lines.extend(rows)
        lines.append(
            f"read one: `agforge knowledge show {source.name}/<path>` · "
            f"search: `agforge knowledge search <terms>`"
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --- one file ------------------------------------------------------------------


def resolve(ref: str, sources: list[Source] | None = None) -> tuple[Source, Path]:
    """`<source>/<relative path>` → (source, absolute path), inside the source.

    A reference that climbs out of its source resolves to nothing: the
    library is the two checkouts, not the disk.
    """
    found = load_sources() if sources is None else sources
    name, _, rest = str(ref or "").strip().strip("/").partition("/")
    for source in found:
        if source.name == name:
            root = source.path.resolve()
            target = (root / rest).resolve() if rest else root
            if target != root and root not in target.parents:
                raise KnowledgeError(f"{ref!r} reaches outside {source.name}")
            if not target.exists():
                raise KnowledgeError(f"{ref!r}: no such file in {source.name}")
            return source, target
    known = ", ".join(source.name for source in found) or "none configured"
    raise KnowledgeError(f"{ref!r}: no source named {name!r} (sources: {known})")


def show(ref: str, sources: list[Source] | None = None) -> str:
    """A file's text, or a directory's entries one per line."""
    source, target = resolve(ref, sources)
    if target.is_dir():
        names = []
        for child in sorted(target.iterdir()):
            if child.name in SKIPPED_DIRS:
                continue
            names.append(child.name + ("/" if child.is_dir() else ""))
        return "\n".join(names)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"{ref}: binary file, {target.stat().st_size} bytes"
    if len(text) > SHOW_LIMIT:
        text = text[:SHOW_LIMIT] + f"\n… truncated at {SHOW_LIMIT} characters"
    return text


def path_of(ref: str, sources: list[Source] | None = None) -> str:
    return str(resolve(ref, sources)[1])


# --- search ------------------------------------------------------------------


def _files(source: Source):
    if not source.path.is_dir():
        return
    for path in sorted(source.path.rglob("*")):
        if any(part in SKIPPED_DIRS for part in path.relative_to(source.path).parts):
            continue
        if path.is_file() and path.suffix in SEARCH_SUFFIXES:
            yield path


def search(terms, sources: list[Source] | None = None, limit: int = SEARCH_LIMIT) -> list[str]:
    """`<source>/<path>:<line>: <text>` for every line holding every term.

    Case-insensitive substring match, all terms on one line. Bounded, and
    the bound is said when it is hit, so a run knows to narrow rather than
    believe it saw everything.
    """
    wanted = [str(term).lower() for term in terms if str(term).strip()]
    if not wanted:
        return []
    found = load_sources() if sources is None else sources
    hits: list[str] = []
    for source in found:
        for path in _files(source):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(source.path)
            for number, line in enumerate(lines, 1):
                lowered = line.lower()
                if all(term in lowered for term in wanted):
                    hits.append(f"{source.name}/{rel}:{number}: {line.strip()}")
                    if len(hits) >= limit:
                        hits.append(f"… stopped at {limit} hits; add a term to narrow")
                        return hits
    return hits
