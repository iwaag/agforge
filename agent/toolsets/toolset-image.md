# Description
General image generation & editing tools

# Image Tools

Call thse by the bare name.

- `agforge image generate [--model NAME] [--width W --height H] [--steps N]
  [--cfgscale N] [--seed N] [--ttl MINUTES] [--init-image PATH
  --init-creativity F] "<prompt>"` — generates one image and returns
  time-limited download URL as its final line. `--init-image` starts from a
  reference image (image-to-image): its composition, framing and palette
  steer the result and the prompt says what changes; `--init-creativity`
  is how far to depart from it (0 keeps it, 1 ignores it, default 0.6).
  A published reference's file is `agrefs path <source>@<rev>:<path>`.
- Standard file writing inside your cwd is allowed.
- `curl -sL "<url>" -o name.png` — fetch a generated image into your cwd so
  you can work on it.
- `uv run python` — a Python 3 with **Pillow** available. 
- `sips -g pixelWidth -g pixelHeight file.png` — quick inspection. `sips` can
  also resize, crop, rotate and convert formats, but it cannot remove a
  background.
- `file out.png` — confirm what you actually produced (it reports `RGBA` when
  an alpha channel is really there).
- `jq` — for reading any JSON you produce or fetch.

# Knowledge

- `agforge knowledge list` — what is known about making images here: general
  study knowledge and the localised capabilities that run on this
  environment, each with a state. `agforge knowledge show <source>/<path>`
  reads one file; `agforge knowledge search <terms>` finds lines.
- A localised capability is a folder with a README saying how to run it.
  Run its script in place: `uv run --with pillow --with numpy --with requests
  python "$(agforge knowledge path localize/<folder>/<script>.py)" --help`.
  The README says which packages it needs. Host-specific values it reads
  live beside it in its own ignored config; you do not need to know them.
- `agforge image generate` is one way to get an image (SwarmUI, the default
  checkpoint). A localised capability may drive ComfyUI directly with other
  checkpoints; its README says which.
