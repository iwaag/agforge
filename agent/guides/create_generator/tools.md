## Tools

- `generate.sh [--model NAME] [--width W --height H] [--steps N]
  [--cfgscale N] [--seed N] [--ttl MINUTES] "<prompt>"` — generates one
  image. It prints a time-limited download URL as its final line; the image
  itself is not left in your cwd.

  Call it by the bare name, exactly as written above. It is on your `PATH`,
  **not** in your working directory — `./generate.sh` will not find it.
- Standard file writing inside your cwd is allowed.
- `curl -sL "<url>" -o name.png` — fetch a generated image into your cwd so
  you can work on it.

## Editing images after generating them

`generate.sh` only makes an image from a prompt. Anything the prompt cannot
express — a real alpha channel, an exact crop, a palette, a sprite sheet — is
done afterwards, with these.

- `uv run python` — a Python 3 with **Pillow** available. This runs from
  anywhere under the agforge tree, including your cwd, so a one-liner or a
  small script you write is enough:

  ```sh
  uv run python -c "
  from PIL import Image
  im = Image.open('in.png').convert('RGBA')
  px = im.load()
  for y in range(im.height):
      for x in range(im.width):
          if px[x, y][:3] == (255, 0, 255):   # the magenta you asked for
              px[x, y] = (0, 0, 0, 0)
  im.save('out.png')
  "
  ```

  That is the usual way to get a **transparent background**: generate on a
  flat, unnatural background colour, then key that colour out to alpha. Pillow
  also covers resizing, cropping, quantizing to a fixed palette, compositing,
  and format conversion.

- `sips -g pixelWidth -g pixelHeight file.png` — quick inspection. `sips` can
  also resize, crop, rotate and convert formats, but it cannot remove a
  background.
- `file out.png` — confirm what you actually produced (it reports `RGBA` when
  an alpha channel is really there, which is worth checking before you claim
  transparency).
- `jq` — for reading any JSON you produce or fetch.

Not installed on this host: ImageMagick (`magick`) and `ffmpeg`. Plan with
Pillow instead; it covers the same still-image ground.

## Audio: `acestudio-cli`

Singing-voice audio, by driving a running ACE Studio. It documents itself —
`acestudio-cli help` shows the docs for a command or topic and regex-searches
across them, and `--help` on any subcommand lists what it takes. Read those
rather than guessing.

It only works when ACE Studio is actually running with External Agent Access
enabled, which is often not the case. **Probe before planning around it:**

```sh
acestudio-cli status project
```

Watch the output, not the exit code: it answers `bridge not reachable …` and
still exits `0`. If it is not reachable, an audio deliverable is not something
you can produce right now — that is an `idea.md`, not a `plan.md`.