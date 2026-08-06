# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "boto3"]
# ///
"""Generate one image via SwarmUI, upload it to MinIO, print a download URL.

Usage:
    uv run scripts/generate.py [--ttl MINUTES] "a prompt"

Prints the local file path, then the presigned download URL as the FINAL
line of output. Reads configuration from `.local/.env` (see README_DEV.md
for keys). Generation parameters beyond the prompt and model are left unset
on purpose so the settings currently configured in the SwarmUI web UI apply.
"""

import argparse
import sys
import uuid
from datetime import date
from pathlib import Path

import boto3
import requests

DEFAULT_TTL_MINUTES = 60

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = AGFORGE_ROOT / ".local" / "out"


def load_env() -> dict[str, str]:
    env_file = AGFORGE_ROOT / ".local" / ".env"
    if not env_file.exists():
        sys.exit(f"missing {env_file} — see README_DEV.md for the expected keys")
    env = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


# Optional generation params, read from .local/.env when present. The running
# SwarmUI (0.9.7.4) requires at least `model`; everything else falls back to
# the server's current UI defaults.
ENV_PARAMS = {
    "AGFORGE_SWARMUI_MODEL": "model",
    "AGFORGE_SWARMUI_WIDTH": "width",
    "AGFORGE_SWARMUI_HEIGHT": "height",
    "AGFORGE_SWARMUI_STEPS": "steps",
    "AGFORGE_SWARMUI_CFGSCALE": "cfgscale",
    "AGFORGE_SWARMUI_SEED": "seed",
}


def generate_image(swarmui_url: str, prompt: str, env: dict[str, str]) -> Path:
    base = swarmui_url.rstrip("/")
    session = requests.post(f"{base}/API/GetNewSession", json={}, timeout=30)
    session.raise_for_status()
    session_id = session.json()["session_id"]

    payload = {"session_id": session_id, "prompt": prompt, "images": 1}
    for env_key, param in ENV_PARAMS.items():
        if env.get(env_key):
            payload[param] = env[env_key]
    resp = requests.post(
        f"{base}/API/GenerateText2Image",
        json=payload,
        timeout=600,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        sys.exit(f"SwarmUI error: {data['error']}")
    images = data.get("images") or []
    if not images:
        sys.exit(f"SwarmUI returned no images: {data}")

    image_ref = images[0]
    if image_ref.startswith("data:"):
        sys.exit("data URLs not supported; disable base64 output in SwarmUI")
    image_url = image_ref if image_ref.startswith("http") else f"{base}/{image_ref}"
    download = requests.get(image_url, timeout=120)
    download.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(image_ref.split("?")[0]).suffix or ".png"
    local_path = OUT_DIR / f"{date.today().isoformat()}-{uuid.uuid4().hex[:8]}{suffix}"
    local_path.write_bytes(download.content)
    return local_path


def upload_and_presign(env: dict[str, str], local_path: Path, ttl_minutes: int) -> str:
    required = [
        "AGFORGE_S3_ENDPOINT",
        "AGFORGE_S3_BUCKET",
        "AGFORGE_S3_ACCESS_KEY",
        "AGFORGE_S3_SECRET_KEY",
    ]
    missing = [k for k in required if not env.get(k)]
    if missing:
        sys.exit(f"missing in .local/.env: {', '.join(missing)}")
    bucket = env["AGFORGE_S3_BUCKET"]
    if bucket == "nctl-outbox":
        sys.exit("refusing to write to the nctl-outbox bucket")

    # endpoint_url is signed into the presigned URL — it must be the hostname
    # recipients can reach, never localhost.
    s3 = boto3.client(
        "s3",
        endpoint_url=env["AGFORGE_S3_ENDPOINT"],
        aws_access_key_id=env["AGFORGE_S3_ACCESS_KEY"],
        aws_secret_access_key=env["AGFORGE_S3_SECRET_KEY"],
        region_name="us-east-1",
    )
    key = f"images/{date.today().isoformat()}/{uuid.uuid4().hex}{local_path.suffix}"
    content_type = "image/jpeg" if local_path.suffix in (".jpg", ".jpeg") else "image/png"
    s3.upload_file(str(local_path), bucket, key, ExtraArgs={"ContentType": content_type})
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_minutes * 60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="text prompt for the image")
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL_MINUTES,
        metavar="MINUTES",
        help=f"presigned URL lifetime in minutes (default {DEFAULT_TTL_MINUTES})",
    )
    args = parser.parse_args()
    if not args.prompt.strip():
        parser.error("prompt is empty")

    env = load_env()
    swarmui_url = env.get("AGFORGE_SWARMUI_URL")
    if not swarmui_url:
        sys.exit("AGFORGE_SWARMUI_URL missing from .local/.env")
    local_path = generate_image(swarmui_url, args.prompt, env)
    print(f"local: {local_path}", file=sys.stderr)
    url = upload_and_presign(env, local_path, args.ttl)
    print(url)


if __name__ == "__main__":
    main()
