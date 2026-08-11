"""Post-process and upload an agforge asset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from . import generate as generate_module


def transform(
    local_path: Path,
    width: int | None = None,
    height: int | None = None,
    target_format: str | None = None,
) -> Path:
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
                out = out.convert("RGB")
        out_path = local_path.with_name(f"{local_path.stem}-{out.size[0]}x{out.size[1]}{suffix}")
        out.save(out_path, format=save_format)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="local image file to process")
    parser.add_argument("--format", choices=["png", "jpeg"], dest="target_format")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--ttl", type=int, default=generate_module.DEFAULT_TTL_MINUTES, metavar="MINUTES")
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
