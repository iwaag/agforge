"""Deterministic tests for the post-processing tool (agentify ex3).

Only the local Pillow mechanics are pinned here — upload/presign is
generate.py's job and needs live MinIO, so it stays out of unit tests.
"""

from pathlib import Path

import pytest
from PIL import Image

import transform


@pytest.fixture
def jpeg_file(tmp_path) -> Path:
    path = tmp_path / "gen.jpg"
    Image.new("RGB", (320, 320), color=(200, 30, 30)).save(path, format="JPEG")
    return path


def read_back(path: Path) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as image:
        return (image.format or "").lower(), image.size


def test_no_flags_is_a_passthrough(jpeg_file):
    assert transform.transform(jpeg_file) == jpeg_file


def test_convert_jpeg_to_png(jpeg_file):
    out = transform.transform(jpeg_file, target_format="png")
    assert out.suffix == ".png"
    fmt, size = read_back(out)
    assert (fmt, size) == ("png", (320, 320))


def test_resize_and_convert(jpeg_file):
    out = transform.transform(jpeg_file, width=300, height=300, target_format="png")
    fmt, size = read_back(out)
    assert (fmt, size) == ("png", (300, 300))
    assert "300x300" in out.name


def test_single_dimension_keeps_aspect_ratio(tmp_path):
    path = tmp_path / "wide.png"
    Image.new("RGB", (640, 320)).save(path, format="PNG")
    out = transform.transform(path, width=200)
    _, size = read_back(out)
    assert size == (200, 100)


def test_png_with_alpha_to_jpeg_drops_alpha(tmp_path):
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (64, 64), color=(0, 0, 0, 0)).save(path, format="PNG")
    out = transform.transform(path, target_format="jpeg")
    assert out.suffix == ".jpg"
    fmt, _ = read_back(out)
    assert fmt == "jpeg"
