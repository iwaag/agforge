"""Stub for AGFORGE_GENERATE_CMD: writes a real PNG of a configurable size.

Mimics generate.sh's observable contract: `local: <path>` on stderr, a
presigned-looking URL as the last stdout line. Stdlib only (runs outside
the pytest venv is fine).

Env contract:
    FAKE_GEN_OUT    — directory to write PNGs into (required).
    FAKE_GEN_SIZES  — JSON list of "WxH" per call, last entry repeats
                      (requires FAKE_GEN_STATE counter file); when unset,
                      the --width/--height flags are honored (512x512 when
                      absent), i.e. an obedient generator.
    FAKE_GEN_MARKER — when set, touch this file on every invocation (lets
                      tests assert generate was NOT reached).
"""

import argparse
import json
import os
import struct
import sys
import zlib
from pathlib import Path


def write_png(path: Path, width: int, height: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("prompt")
    args = parser.parse_args()

    marker = os.environ.get("FAKE_GEN_MARKER")
    if marker:
        Path(marker).write_text("called")

    calls = 0
    state = os.environ.get("FAKE_GEN_STATE")
    if state:
        state_path = Path(state)
        calls = int(state_path.read_text() or "0") if state_path.exists() else 0
        state_path.write_text(str(calls + 1))

    sizes = os.environ.get("FAKE_GEN_SIZES")
    if sizes:
        entries = json.loads(sizes)
        width, height = map(int, entries[min(calls, len(entries) - 1)].split("x"))
    else:
        width = args.width if args.width is not None else 512
        height = args.height if args.height is not None else 512

    out_dir = Path(os.environ["FAKE_GEN_OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)
    local_path = out_dir / f"fake-{calls}-{width}x{height}.png"
    write_png(local_path, width, height)
    print(f"local: {local_path}", file=sys.stderr)
    print(f"http://fake.example/generated/{local_path.name}")


if __name__ == "__main__":
    main()
