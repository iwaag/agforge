"""Generate one image via SwarmUI, upload it to MinIO, and print its URL."""

from __future__ import annotations

import argparse
import sys
import tomllib
import uuid
from datetime import date
from pathlib import Path

import boto3
import requests

DEFAULT_TTL_MINUTES = 60

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = AGFORGE_ROOT / ".local" / "out"
DEFAULTS_FILE = AGFORGE_ROOT / "params" / "defaults.toml"

PARAM_NAMES = ["model", "width", "height", "steps", "cfgscale", "seed"]
ENV_KEYS = {
    "model": "AGFORGE_SWARMUI_MODEL",
    "width": "AGFORGE_SWARMUI_WIDTH",
    "height": "AGFORGE_SWARMUI_HEIGHT",
    "steps": "AGFORGE_SWARMUI_STEPS",
    "cfgscale": "AGFORGE_SWARMUI_CFGSCALE",
    "seed": "AGFORGE_SWARMUI_SEED",
}


def load_env() -> dict[str, str]:
    env_file = AGFORGE_ROOT / ".local" / ".env"
    if not env_file.exists():
        sys.exit(f"missing {env_file} — see README_DEV.md for the expected keys")
    env = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def load_defaults() -> dict[str, str]:
    if not DEFAULTS_FILE.exists():
        return {}
    with DEFAULTS_FILE.open("rb") as file:
        data = tomllib.load(file)
    return {key: str(value) for key, value in data.get("defaults", {}).items()}


def resolve_params(
    defaults: dict[str, str], env: dict[str, str], cli_args: argparse.Namespace
) -> dict[str, str]:
    params = dict(defaults)
    for name, env_key in ENV_KEYS.items():
        if env.get(env_key):
            params[name] = env[env_key]
    for name in PARAM_NAMES:
        cli_value = getattr(cli_args, name)
        if cli_value is not None:
            params[name] = str(cli_value)
    if not params.get("model"):
        sys.exit(
            "model is required but not set anywhere: not in "
            f"{DEFAULTS_FILE.relative_to(AGFORGE_ROOT)}, not as "
            "AGFORGE_SWARMUI_MODEL in .local/.env, and no --model flag. "
            "List valid names with POST /API/ListModels against your SwarmUI instance."
        )
    return params


def generate_image(swarmui_url: str, prompt: str, params: dict[str, str]) -> Path:
    base = swarmui_url.rstrip("/")
    session = requests.post(f"{base}/API/GetNewSession", json={}, timeout=30)
    session.raise_for_status()
    session_id = session.json()["session_id"]

    payload = {"session_id": session_id, "prompt": prompt, "images": 1}
    payload.update(params)
    response = requests.post(f"{base}/API/GenerateText2Image", json=payload, timeout=600)
    response.raise_for_status()
    data = response.json()
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
        "AGFORGE_S3_ENDPOINT", "AGFORGE_S3_BUCKET",
        "AGFORGE_S3_ACCESS_KEY", "AGFORGE_S3_SECRET_KEY",
    ]
    missing = [key for key in required if not env.get(key)]
    if missing:
        sys.exit(f"missing in .local/.env: {', '.join(missing)}")
    bucket = env["AGFORGE_S3_BUCKET"]
    if bucket == "nctl-outbox":
        sys.exit("refusing to write to the nctl-outbox bucket")

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
        "--ttl", type=int, default=DEFAULT_TTL_MINUTES, metavar="MINUTES",
        help=f"presigned URL lifetime in minutes (default {DEFAULT_TTL_MINUTES})",
    )
    parser.add_argument("--model", help="overrides defaults.toml / .local/.env")
    parser.add_argument("--width", help="overrides defaults.toml / .local/.env")
    parser.add_argument("--height", help="overrides defaults.toml / .local/.env")
    parser.add_argument("--steps", help="overrides defaults.toml / .local/.env")
    parser.add_argument("--cfgscale", help="overrides defaults.toml / .local/.env")
    parser.add_argument("--seed", help="overrides defaults.toml / .local/.env")
    args = parser.parse_args()
    if not args.prompt.strip():
        parser.error("prompt is empty")

    env = load_env()
    swarmui_url = env.get("AGFORGE_SWARMUI_URL")
    if not swarmui_url:
        sys.exit("AGFORGE_SWARMUI_URL missing from .local/.env")
    params = resolve_params(load_defaults(), env, args)
    local_path = generate_image(swarmui_url, args.prompt, params)
    print(f"local: {local_path}", file=sys.stderr)
    print(upload_and_presign(env, local_path, args.ttl))


if __name__ == "__main__":
    main()
