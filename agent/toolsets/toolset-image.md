# Description
General image generation & editing tools

# Image Tools

Call thse by the bare name.

- `agforge image generate [--model NAME] [--width W --height H] [--steps N]
  [--cfgscale N] [--seed N] [--ttl MINUTES] "<prompt>"` — generates one
  image and returns time-limited download URL as its final line;
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

