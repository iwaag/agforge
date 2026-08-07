# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "boto3", "requests"]
# ///
"""Retired pipeline steps kept as candidate agent tools (agentify ex2).

Nothing calls these automatically anymore. They were the deterministic
resize/convert stage of the pre-ex2 pipeline; if live agent runs show
resize/convert to be a recurring mechanical step, these earn their way
back as tools offered to the agent (see the ex2 report's hardening
candidates).
"""

import sys
from pathlib import Path

from PIL import Image

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGFORGE_ROOT / "scripts"))
import generate as generate_module  # noqa: E402


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def image_format(path: Path) -> str:
    """Actual file format as pipeline vocabulary: "png", "jpeg", or other."""
    with Image.open(path) as image:
        fmt = (image.format or "").lower()
    return "jpeg" if fmt in ("jpeg", "jpg") else fmt


def transform_and_upload(
    local_path: Path, target_w: int | None, target_h: int | None, target_format: str | None
) -> str:
    """Deterministically resize and/or convert a generated image, re-presign.

    Any None argument means keep as generated. A single unset dimension is
    derived from the actual aspect ratio.
    """
    with Image.open(local_path) as image:
        out = image
        if target_w is not None or target_h is not None:
            actual_w, actual_h = image.size
            width = target_w if target_w is not None else max(1, round(actual_w * target_h / actual_h))
            height = target_h if target_h is not None else max(1, round(actual_h * target_w / actual_w))
            out = out.resize((width, height), Image.LANCZOS)
        suffix, save_format = local_path.suffix, None
        if target_format == "png":
            suffix, save_format = ".png", "PNG"
        elif target_format == "jpeg":
            suffix, save_format = ".jpg", "JPEG"
            if out.mode not in ("RGB", "L"):
                out = out.convert("RGB")  # JPEG cannot carry alpha
        out_path = local_path.with_name(
            f"{local_path.stem}-{out.size[0]}x{out.size[1]}{suffix}"
        )
        out.save(out_path, format=save_format)
    env = generate_module.load_env()
    return generate_module.upload_and_presign(
        env, out_path, generate_module.DEFAULT_TTL_MINUTES
    )
