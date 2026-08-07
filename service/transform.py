# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "boto3", "requests"]
# ///
"""Post-processing tool offered to the request agent (agentify ex3).

Earned by recurrence: every ex2 format run showed the same mechanical
jpeg->png convert -> verify -> re-upload sequence, so the retired
pipeline step (ex2's `candidate_tools.transform_and_upload`) is now a
one-line command the agent may call. The agent still DECIDES whether to
post-process; only the mechanics live here.

Usage:
    uv run service/transform.py [--format png|jpeg] [--width W] [--height H] <file>

- Resizes and/or converts the local image, uploads the result to the
  agforge bucket, and prints the fresh presigned URL as the FINAL line
  of stdout. The produced local file path goes to stderr as
  `local: <path>`.
- With no flags the file is uploaded as-is — the sanctioned upload path
  for any post-processing the agent invents itself (no hand-rolled S3).
- A single unset dimension is derived from the actual aspect ratio.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGFORGE_ROOT / "scripts"))
import generate as generate_module  # noqa: E402


def transform(
    local_path: Path,
    width: int | None = None,
    height: int | None = None,
    target_format: str | None = None,
) -> Path:
    """Resize and/or convert; return the produced file (input if no-op)."""
    if width is None and height is None and target_format is None:
        return local_path
    with Image.open(local_path) as image:
        out = image
        if width is not None or height is not None:
            actual_w, actual_h = image.size
            w = width if width is not None else max(1, round(actual_w * height / actual_h))
            h = height if height is not None else max(1, round(actual_h * width / actual_w))
            out = out.resize((w, h), Image.LANCZOS)
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
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="local image file to process")
    parser.add_argument(
        "--format", choices=["png", "jpeg"], dest="target_format",
        help="convert to this format (default: keep as-is)",
    )
    parser.add_argument("--width", type=int, help="target width in pixels")
    parser.add_argument("--height", type=int, help="target height in pixels")
    parser.add_argument(
        "--ttl", type=int, default=generate_module.DEFAULT_TTL_MINUTES,
        metavar="MINUTES", help="presigned URL lifetime in minutes",
    )
    args = parser.parse_args()
    local_path = Path(args.file)
    if not local_path.is_file():
        sys.exit(f"not a file: {local_path}")
    out_path = transform(local_path, args.width, args.height, args.target_format)
    print(f"local: {out_path}", file=sys.stderr)
    env = generate_module.load_env()
    print(generate_module.upload_and_presign(env, out_path, args.ttl))


if __name__ == "__main__":
    main()
