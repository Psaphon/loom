# First Project: Palm-Tree Demo

This guide walks through building a complete music video from scratch using a palm-tree clip as base footage and a simple overlay. By the end you will have a rendered `output/final.mp4` you can play back.

---

## What you need

| File | Description |
|------|-------------|
| `input/base.mp4` | 10–30 second palm-tree clip (any resolution) |
| `input/song.mp3` | Any song, MP3 or WAV |
| `input/overlays/particles.mp4` | Short overlay clip (looping particles, abstract shapes, etc.) |

Any freely-licensed stock clips work. Resolution and length do not need to match — loom scales and loops automatically.

ComfyUI must be running before the preprocess and pipeline stages. See [`docs/comfyui-setup.md`](comfyui-setup.md).

---

## Step 1 — Create the project directory

```bash
mkdir -p ~/Projects/palm-demo/input/overlays
mkdir -p ~/Projects/palm-demo/output
cd ~/Projects/palm-demo
```

Copy your clips in:

```bash
cp /path/to/palm-tree.mp4    input/base.mp4
cp /path/to/song.mp3         input/song.mp3
cp /path/to/particles.mp4    input/overlays/particles.mp4
```

---

## Step 2 — Write the config

Create `loom.toml`:

```toml
[project]
name = "palm-demo"

[paths]
base     = "input/base.mp4"
song     = "input/song.mp3"
output   = "output/"
overlays = ["input/overlays/particles.canny.mp4"]

[render]
width            = 512
height           = 512
fps              = 24
enable_diffusion = false

[[timeline]]
section = "intro"
opacity = 0.4
blend   = "screen"

[[timeline]]
section = "chorus"
opacity = 0.8
blend   = "screen"
```

`enable_diffusion = false` skips the AnimateDiff pass — faster, and the right starting point before committing to a 4-hour render.

---

## Step 3 — Analyze song structure

```bash
loom analyze --song input/song.mp3
```

This writes `input/song.structure.json`. Open it:

```json
{
  "tempo_bpm": 128.0,
  "duration_seconds": 213.4,
  "beats": [0.35, 0.81, 1.28, ...],
  "sections": [
    { "start": 0.0,   "end": 32.1,   "label": "section_0" },
    { "start": 32.1,  "end": 96.3,   "label": "section_1" },
    { "start": 96.3,  "end": 213.4,  "label": "section_2" }
  ]
}
```

Rename the sections to match the `[[timeline]]` labels you wrote in the config:

```json
{
  "sections": [
    { "start": 0.0,   "end": 32.1,   "label": "intro"  },
    { "start": 32.1,  "end": 96.3,   "label": "chorus" },
    { "start": 96.3,  "end": 213.4,  "label": "outro"  }
  ]
}
```

The labels must match exactly (case-sensitive). Sections not covered by a `[[timeline]]` rule use `opacity = 1.0`, `blend = "normal"`.

---

## Step 4 — Preprocess the overlay

```bash
loom preprocess \
  --input  input/overlays/particles.mp4 \
  --style  canny \
  --output input/overlays/particles.canny.mp4
```

This sends each frame through ComfyUI's `CannyEdgePreprocessor` and produces a white-on-black edge video. The `screen` blend mode in the config will layer these bright edges over the palm footage.

For a softer look, try `--style hed` or `--style lineart`. See [`docs/preprocess-styles.md`](preprocess-styles.md) for a comparison.

---

## Step 5 — Dry-run to validate config

```bash
loom run --config loom.toml --dry-run
```

This loads and validates config without rendering anything. Fix any errors before proceeding.

---

## Step 6 — Run the pipeline

```bash
loom run --config loom.toml
```

Stages run in order:

```
analyze → preprocess → composite → upscale → mux
```

Progress is logged to stdout and `output/loom.log`. A full run (no diffusion, short clip) typically takes 5–15 minutes.

If interrupted, re-run the same command — completed stages are skipped automatically.

---

## Step 7 — Review the output

```bash
mpv output/final.mp4
```

The result is a 1080p MP4 with:
- Base footage scaled to 512 × 512 for intermediate stages, upscaled to 1080p by Real-ESRGAN
- Canny edge overlay blended with `screen` mode
- Overlay opacity modulated by section (40% intro, 80% chorus)
- Original audio track muxed in without re-encoding

---

## Iterating

Common adjustments after first review:

**Overlay too prominent** — lower `opacity` in the chorus `[[timeline]]` block, re-run.

**Wrong sections** — edit `input/song.structure.json` section boundaries, re-run. The analyze stage is skipped (cached); composite reruns from the corrected structure.

**Different look** — change `--style` in the preprocess step. Delete `input/overlays/particles.canny.mp4`, rerun preprocess and pipeline.

**Enable diffusion** — set `enable_diffusion = true` in `[render]`. This adds an AnimateDiff + ControlNet stylization pass before compositing. Renders at 512 px and takes significantly longer (hours on RTX 2060). See [`docs/hardware.md`](hardware.md) for VRAM guidance.

**Force re-run all stages** — `loom run --config loom.toml --force`

---

## Scheduling it overnight

Once the config is dialled in, hand it off to the systemd timer:

```bash
loom install-systemd --project-dir ~/Projects/palm-demo
```

Follow the output instructions to install and enable the timer. The pipeline will run at 00:00 and stop by 05:30. See [`docs/scheduling.md`](scheduling.md).

---

## Troubleshooting

See [`docs/troubleshooting.md`](troubleshooting.md) for a full failure reference. Quick checks for this walkthrough:

| Problem | Fix |
|---------|-----|
| `ComfyUI not reachable` | Start ComfyUI before running preprocess |
| `Song structure JSON not found` | Run `loom analyze` first |
| Section opacity unchanged | Verify section labels match exactly in both JSON and TOML |
| OOM in preprocess | Lower `render.width` / `render.height` to 512 (already the default) |
| Output is silent | Check `paths.song` in `loom.toml` |
