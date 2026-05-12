# Loom

## What This Is

Overnight media pipeline that renders music videos from a directory of inputs (base footage, overlay clips, song, TOML config). Orchestrates ComfyUI (preprocessing, diffusion, upscaling) and ffmpeg (compositing, muxing). Reads song structure to modulate overlay effects across sections. Runs unattended via systemd timer 00:00–05:30 nightly.

Not a real-time tool. Not a GUI editor. Not cloud-hosted. CLI + config only. Personal use.

## Architecture

```
[systemd timer — 00:00 local, stops at 05:30]
        │
        ▼
[1. Analyze] ── librosa → {song}.structure.json (cached)
        │
        ▼
[2. Stylize*] ── ComfyUI: AnimateDiff + ControlNet (optional, 512px)
        │
        ▼
[3. Preprocess] ── ComfyUI: lineart / canny / depth / hed on overlays
        │
        ▼
[4. Composite] ── ffmpeg filter_complex, blend modes, opacity modulated
                  by song structure
        │
        ▼
[5. Upscale] ── ComfyUI: Real-ESRGAN to 1080p
        │
        ▼
[6. Mux] ── ffmpeg mux audio, no video re-encode → output/final.mp4

(* = optional via config flag)
```

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Language | Python 3.11+ | `tomllib` stdlib, modern typing |
| CLI | Click | Consistent with morning-brief |
| Config | TOML (stdlib `tomllib`) | No dep, hand-editable |
| HTTP | httpx (async) | ComfyUI API calls |
| Audio | librosa | Mature, offline, no API cost |
| Video | ffmpeg (subprocess) | Filter graphs + muxing |
| Diffusion/preproc/upscale | ComfyUI (external HTTP API) | Loom does not manage it |
| Scheduling | systemd user timer | 00:00–05:30 GPU window |

## Project Structure

```
loom/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── docs/
│   ├── BRIEF.md             ← project pitch
│   ├── DEVPLAN.md           ← feature plan for AI development
│   ├── config.md            ← TOML config schema
│   ├── comfyui-setup.md     ← external ComfyUI requirements
│   └── …
├── src/loom/
│   ├── cli.py               ← Click entry, subcommands
│   ├── config.py            ← TOML loader + validator
│   ├── analysis.py          ← librosa song-structure
│   ├── comfy.py             ← async ComfyUI client
│   ├── preprocess.py        ← overlay abstractors
│   ├── stylize.py           ← AnimateDiff + ControlNet
│   ├── composite.py         ← ffmpeg filter_complex
│   ├── timeline.py          ← song structure → per-frame params
│   ├── upscale.py           ← Real-ESRGAN
│   ├── video_io.py          ← ffmpeg helpers
│   ├── pipeline.py          ← stage orchestration, deadline, retries
│   ├── state.py             ← resumable state file
│   ├── systemd.py           ← unit file templating
│   └── schemas.py           ← SongStructure, Section, Beat
├── workflows/               ← ComfyUI workflow JSONs (assets, not code)
│   ├── preprocess/{lineart,canny,depth,hed}.json
│   ├── stylize/animatediff_controlnet.json
│   └── upscale/real_esrgan.json
├── templates/
│   ├── loom.service.j2
│   └── loom.timer.j2
├── tests/
└── output/                  ← renders + state (gitignored)
```

## Constraints

- **GPU ceiling: RTX 2060, 6GB VRAM.** No feature may assume more. Diffusion at 512px, upscale separately.
- **Must stop cleanly by 05:30.** Morning-brief owns the GPU after that.
- **ComfyUI is external.** Loom assumes it is running; never installs or manages it.
- **Nothing persistent in `/home`.** State lives in project dir, SECRETS USB, or named Docker volumes.
- **All config via env vars or TOML.** Never hardcoded.
- **Workflow JSONs are assets**, committed but not linted.
- **OOM must fail cleanly**, not hang or loop.

## Commit Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`
- Gitflow: `main`, `develop`, `feature/*`, `fix/*`, `release/*`, `hotfix/*`
- Feature branches merge to `develop` via PR. Auto-merge enabled; no human review per PR.

## Code Standards

**CRITICAL: Run lint + tests before EVERY commit. No exceptions.**

```bash
ruff check . && ruff format --check .
pytest
```

Never `--no-verify`. A commit that fails lint is broken.

- Use `logging` (never `print`)
- Use `httpx` (async) for HTTP
- Use `pathlib.Path`, never string paths
- Test with mocked ComfyUI (respx or equivalent)

## Key Decisions

1. **ComfyUI external** — loom orchestrates, does not embed or install it
2. **librosa for song structure** — offline, free, good enough; JSON is hand-editable
3. **ffmpeg for compositing, not OpenCV** — filter graphs are more expressive for blend modes
4. **Frame-batch ComfyUI calls** — amortize HTTP overhead (default 16 frames/call)
5. **Resumable state file** — a 4-hour window may not fit a full render; three-night completion is acceptable
6. **Max 2 retries per stage** — prevent infinite loops on broken renders
7. **SD1.5-based AnimateDiff, not newer models** — 6GB VRAM budget

## Configuration

Config schema documented in `docs/config.md`. Project TOML includes:
- Paths (base footage, overlays, song, output)
- Render settings (resolution, fps, `enable_diffusion`)
- Deadline (default `05:30`, via `LOOM_DEADLINE` env var)
- Timeline modulation rules (`[[timeline]] section="chorus" opacity=0.7 blend="screen"`)

## Coordination

- **Morning-brief timer moves from 04:15 → 05:30** as part of loom's systemd rollout. Paired deploy — see `~/Projects/morning-brief/docs/COORDINATION.md`.
- **Ollama is stopped** by loom's service unit (`ExecStartPre`) and restarted on exit (`ExecStopPost`). Ad-hoc for now; dtl may generalize later (see devtools FEATURE-REQUESTS `dtl schedule`).

## Audience and Tone

- Target reader: a self-hosted operator on a single-GPU workstation
- Tone: technical, terse, assumes familiarity with ComfyUI and `~/Projects` conventions
