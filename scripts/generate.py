# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "boto3"]
# ///
"""Generate one image via SwarmUI and print its local file path.

Usage:
    uv run scripts/generate.py "a prompt"

Reads configuration from `.local/.env` (see README_DEV.md for keys).
Generation parameters beyond the prompt are left unset on purpose so the
settings currently configured in the SwarmUI web UI apply.
"""

import sys
import uuid
from datetime import date
from pathlib import Path

import requests

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


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit('usage: uv run scripts/generate.py "a prompt"')
    env = load_env()
    swarmui_url = env.get("AGFORGE_SWARMUI_URL")
    if not swarmui_url:
        sys.exit("AGFORGE_SWARMUI_URL missing from .local/.env")
    local_path = generate_image(swarmui_url, sys.argv[1], env)
    print(local_path)


if __name__ == "__main__":
    main()
