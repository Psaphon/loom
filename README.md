# Loom

Overnight music video renderer. Drop in base footage, overlay clips, and a song; wake up to a composited, upscaled MP4. Runs unattended via systemd timer in a nightly GPU window.

Orchestrates [ComfyUI](https://github.com/comfyanonymous/ComfyUI) (preprocessing, diffusion, upscaling) and ffmpeg (compositing, muxing). Reads song structure to modulate overlay effects across sections.

---

## Hardware requirements

| Component | Minimum | Notes |
|-----------|---------|-------|
| GPU | NVIDIA RTX 2060, 6 GB VRAM | Shapes every model choice — see [`docs/hardware.md`](docs/hardware.md) |
| System RAM | 16 GB | librosa + ffmpeg frame buffers |
| Storage | ~10 GB free | Intermediate renders per project |
| OS | Linux with systemd | User session timer required |

Diffusion runs at 512 px to fit within 6 GB. Real-ESRGAN upscales to 1080p afterward.

---

## Dependencies

### Python packages

```
click>=8.1
httpx>=0.27
librosa>=0.10
numpy>=1.26
```

Installed automatically via `pip install -e .`.

### External (must be present before running)

| Tool | Purpose |
|------|---------|
| **ffmpeg** | Compositing, muxing, frame I/O |
| **ComfyUI** | Preprocessing, diffusion, upscaling |

ffmpeg must be on `$PATH` with libx264 support. ComfyUI must be running at `http://127.0.0.1:8188` before any pipeline stage. See [`docs/comfyui-setup.md`](docs/comfyui-setup.md) for ComfyUI installation and required model checkpoints.

---

## Installation

```bash
git clone <repo> ~/Projects/loom
cd ~/Projects/loom
pip install -e ".[dev]"
loom --help
```

Verify:

```bash
loom version
ffmpeg -version
curl -s http://127.0.0.1:8188/system_stats | python -m json.tool
```

---

## Quick start

```bash
# 1. Analyze song structure (cached; safe to re-run)
loom analyze --song input/song.mp3

# 2. Preprocess overlay clips
loom preprocess --input input/overlays/fx.mp4 --style canny --output input/overlays/fx.canny.mp4

# 3. Run full pipeline
loom run --config project.toml

# Dry-run (validate config only, no rendering)
loom run --config project.toml --dry-run
```

All subcommands accept `--help`.

---

## Project layout

```
my-video/
├── loom.toml                    ← project config (see docs/config.md)
├── input/
│   ├── base.mp4                 ← base footage
│   ├── song.mp3                 ← audio
│   ├── song.structure.json      ← produced by loom analyze; hand-editable
│   └── overlays/
│       ├── fx.mp4               ← raw overlay clip
│       └── fx.canny.mp4         ← preprocessed version
└── output/
    ├── composite.mp4
    ├── upscaled.mp4
    ├── final.mp4                ← finished render with audio
    ├── loom.log
    └── .loom-state.json         ← resume state
```

---

## Config

Minimal `loom.toml`:

```toml
[project]
name = "my-video"

[paths]
base     = "input/base.mp4"
song     = "input/song.mp3"
output   = "output/"
overlays = ["input/overlays/fx.canny.mp4"]
```

Full schema: [`docs/config.md`](docs/config.md)

---

## Pipeline stages

```
analyze → [stylize*] → preprocess → composite → upscale → mux → final.mp4
```

`* optional — set enable_diffusion = true in [render]`

Each stage is idempotent. `output/.loom-state.json` records completed stages so interrupted runs resume where they left off.

See [`docs/pipeline.md`](docs/pipeline.md) for stage details, resume semantics, and exit codes.

---

## First render walkthrough

See [`docs/first-project.md`](docs/first-project.md) for a step-by-step guide using a palm-tree demo project.

---

## Scheduling

Loom is designed to run unattended at 00:00 and stop by 05:30, yielding the GPU to morning-brief.

```bash
# Generate systemd unit files
loom install-systemd --project-dir ~/Projects/my-video

# Install and enable
mkdir -p ~/.config/systemd/user/
cp ~/Projects/my-video/loom.service ~/Projects/my-video/loom.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loom.timer
```

Full scheduling guide: [`docs/scheduling.md`](docs/scheduling.md)

---

## Troubleshooting

See [`docs/troubleshooting.md`](docs/troubleshooting.md) for common failures and fixes.

Quick reference:

| Symptom | First check |
|---------|-------------|
| `ComfyUI not reachable` | Is ComfyUI running? `curl http://127.0.0.1:8188/system_stats` |
| OOM during diffusion | Set `enable_diffusion = false` or reduce `width`/`height` to 512 |
| Pipeline stops at 05:30 | Expected — re-run the next night; state is saved |
| `Song structure JSON not found` | Run `loom analyze --song input/song.mp3` first |
| Section labels don't match | Edit `song.structure.json`; rename `section_0` → `intro`, etc. |

---

## Docs index

| Doc | Contents |
|-----|----------|
| [`docs/config.md`](docs/config.md) | Full TOML config schema |
| [`docs/pipeline.md`](docs/pipeline.md) | Stage diagram, resume, exit codes |
| [`docs/first-project.md`](docs/first-project.md) | Palm-tree demo walkthrough |
| [`docs/hardware.md`](docs/hardware.md) | Why 6 GB shapes every decision |
| [`docs/comfyui-setup.md`](docs/comfyui-setup.md) | ComfyUI install + required models |
| [`docs/song-structure.md`](docs/song-structure.md) | Structure JSON schema, hand-editing |
| [`docs/preprocess-styles.md`](docs/preprocess-styles.md) | lineart / canny / depth / hed guide |
| [`docs/compositing.md`](docs/compositing.md) | Blend modes, timeline config |
| [`docs/scheduling.md`](docs/scheduling.md) | systemd timer install + verification |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Common failures |

---

## Development

```bash
ruff check . && ruff format --check .
pytest
```

Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`). Feature branches merge to `develop` via PR.
