"""The toolset library: what `--list` prints, and how a written-down name
resolves back to a file.

`toolsets --list` is a contract, not a convenience: the front copies its
lines into `toolsets.csv` and both topic flows resolve names out of them
again. The leniency pinned here is deliberate — the names travel through an
agent's copy-paste, so extensions, description tails, quotes and case are
all expected to arrive.
"""

import pytest

from agforge import cli, toolsets


def library(tmp_path):
    root = tmp_path / "toolsets"
    root.mkdir()
    (root / "toolset-image.md").write_text(
        "# Description\nGeneral image generation & editing tools\n\n"
        "# Image Tools\n- agforge image generate\n"
    )
    (root / "toolset-video.md").write_text(
        "# Description\nGeneral video\ngeneration tools\n\n# Video Tools\n"
    )
    (root / "notes.md").write_text("# Description\nnot a toolset\n")
    return root


# --- the listing ------------------------------------------------------------


def test_the_listing_is_one_name_and_description_per_toolset(tmp_path):
    assert toolsets.listing(library(tmp_path)) == [
        "toolset-image, General image generation & editing tools",
        # A description spanning lines is folded onto one: the line is the unit.
        "toolset-video, General video generation tools",
    ]


def test_only_toolset_prefixed_files_are_listed(tmp_path):
    assert toolsets.names(library(tmp_path)) == ["toolset-image", "toolset-video"]


def test_a_file_without_a_description_is_still_listed(tmp_path):
    """Every toolset that exists should be visible to the front; a malformed
    one is evidence, not a reason to hide it."""
    root = library(tmp_path)
    (root / "toolset-bare.md").write_text("# Bare Tools\nno description section\n")
    assert "toolset-bare" in toolsets.listing(root)[0]


def test_the_cli_prints_the_listing(tmp_path, monkeypatch, capsys):
    root = library(tmp_path)
    monkeypatch.setattr(toolsets, "TOOLSETS_DIR", root)
    cli.main(["toolsets", "--list"])
    assert capsys.readouterr().out.splitlines() == toolsets.listing(root)


def test_toolsets_without_list_is_a_usage_error(tmp_path, monkeypatch):
    """A silent success would read to an agent as 'no toolsets exist'."""
    monkeypatch.setattr(toolsets, "TOOLSETS_DIR", library(tmp_path))
    with pytest.raises(SystemExit) as error:
        cli.main(["toolsets"])
    assert error.value.code != 0


# --- resolution -------------------------------------------------------------


def test_a_name_resolves_however_it_was_written_down(tmp_path):
    root = library(tmp_path)
    for written in (
        "toolset-image",
        "toolset-image.md",
        "toolset-image, General image generation & editing tools",
        "  TOOLSET-IMAGE  ",
        '"toolset-image"',
    ):
        assert toolsets.resolve(written, root).name == "toolset-image.md", written


def test_an_unknown_or_empty_name_resolves_to_nothing(tmp_path):
    root = library(tmp_path)
    assert toolsets.resolve("toolset-nope", root) is None
    assert toolsets.resolve("", root) is None
    assert toolsets.resolve("notes", root) is None


def test_a_name_cannot_reach_outside_the_library(tmp_path):
    root = library(tmp_path)
    (tmp_path / "toolset-elsewhere.md").write_text("# Description\nelsewhere\n")
    assert toolsets.resolve("../toolset-elsewhere", root) is None


def test_parse_names_keeps_order_and_drops_noise():
    assert toolsets.parse_names(
        "toolset-image, Images\n\n# a comment\ntoolset-video\ntoolset-image\n"
    ) == ["toolset-image", "toolset-video"]


# --- placement --------------------------------------------------------------


def test_place_copies_what_resolves_and_reports_it(tmp_path):
    root = library(tmp_path)
    target = tmp_path / "tools"
    placed = toolsets.place(["toolset-video", "toolset-nope"], target, root)
    assert placed == ["toolset-video"]
    assert [path.name for path in target.iterdir()] == ["toolset-video.md"]


def test_place_creates_the_directory_even_with_nothing_to_put_in_it(tmp_path):
    target = tmp_path / "tools"
    assert toolsets.place([], target, library(tmp_path)) == []
    assert target.is_dir()
