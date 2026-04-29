# Development Plan: loom

**Status:** Draft
**Created:** 2026-04-12
**Updated:** 2026-04-12

## Overview

Loom is an overnight media pipeline that renders music videos from a directory of inputs (base footage, overlay clips, song, TOML config). It orchestrates ComfyUI (preprocessing, diffusion) and ffmpeg (compositing, muxing), reads song structure to time overlay effects, and runs unattended via systemd timer in a 00:00–05:30 GPU window that doesn't collide with morning-brief.

## Constraints

- Python 3.11+, stdlib-preferred, dependencies justified
- GPU target: NVIDIA RTX 2060, 6GB VRAM. No feature may assume more.
- Must not run while morning-brief runs. Pipeline stops cleanly by 05:30.
- All state in git, SECRETS USB, or named Docker volumes. Nothing persistent in /home.
- All config via env vars or checked-in config files, never hardcoded.
- Logging via `logging` module, never `print`. `pathlib.Path`, never string paths.
- Ruff-clean, tests pass, conventional commits, feature branches to develop via PR.
- ComfyUI is an external dependency. Loom assumes it's running; does not install or manage it.

---

## Feature: project-scaffold

**Branch:** `feature/project-scaffold`
**Depends on:** none
**Status:** Complete
**Requires:** ai

### Goal

Create the loom CLI skeleton, package layout, and config loading. Establishes the `loom` Click entry point and proves the package imports cleanly.

### Acceptance Criteria

- [ ] `loom --help` prints subcommand list
- [ ] `loom version` prints package version
- [ ] `loom run --config path/to/config.toml` loads and validates config, exits cleanly (no rendering yet)
- [ ] Config schema documented in `docs/config.md`
- [ ] Invalid config produces a clear error, not a stack trace
- [ ] All tests pass (`pytest`)
- [ ] Lint clean (`ruff check . && ruff format --check .`)

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Create | Package metadata, deps (Click, httpx, librosa, numpy) |
| `src/loom/__init__.py` | Create | Package init, version |
| `src/loom/cli.py` | Create | Click CLI entry, subcommands |
| `src/loom/config.py` | Create | Config dataclass + TOML loader + validator |
| `docs/config.md` | Create | Config schema reference |
| `tests/test_config.py` | Create | Config validation tests |
| `README.md` | Create | Stub (real README is the final feature) |

### Key Decisions

- TOML config, loaded via stdlib `tomllib` (3.11+), no dep.
- Layout: `src/loom/`. Consistent with morning-brief.
- Subcommands: `run`, `analyze`, `preprocess`, `composite`, `stylize`, `install-systemd`.

### Notes

Config must include: project name, paths (base footage, overlays, song, output), render settings (resolution, fps, enable_diffusion flag), schedule (deadline), timeline modulation rules.

---

## Feature: song-analysis

**Branch:** `feature/song-analysis`
**Depends on:** project-scaffold
**Status:** Complete
**Requires:** ai

### Goal

Analyze a song with librosa and produce a hand-editable JSON of structure (tempo, beats, sections). Cache result; downstream renders read the cache.

### Acceptance Criteria

- [ ] `loom analyze --song path/to/song.mp3` produces `{song_name}.structure.json` beside the source
- [ ] JSON includes: tempo_bpm, duration_seconds, beats (list of timestamps), sections (list of {start, end, label})
- [ ] Re-running skips analysis if JSON exists and song mtime unchanged (unless `--force`)
- [ ] Handles MP3, WAV, M4A
- [ ] JSON indented, human-readable, hand-editable
- [ ] Clear error on missing song file
- [ ] All tests pass
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/analysis.py` | Create | librosa wrapper, beat/section detection |
| `src/loom/cli.py` | Modify | Add `analyze` subcommand |
| `src/loom/schemas.py` | Create | Dataclasses for SongStructure, Section, Beat |
| `tests/test_analysis.py` | Create | Tests with generated sample audio |
| `tests/conftest.py` | Create | Fixtures, including synthesized sample audio |
| `docs/song-structure.md` | Create | JSON schema reference, hand-editing guide |

### Key Decisions

- librosa for analysis: mature, offline, no API cost.
- Beats only in v1. Stem-reactive modulation deferred to nice-to-have.
- Section detection via librosa's `segment.agglomerative`. Imperfect; JSON is hand-editable to correct.

### Notes

librosa first-import is slow (numba warmup). Acceptable — runs once per song, cached.

---

## Feature: comfyui-client

**Branch:** `feature/comfyui-client`
**Depends on:** project-scaffold
**Status:** Merged
**Requires:** ai

### Goal

Async HTTP client for ComfyUI API. Submits workflow JSONs, polls for completion, fetches outputs. Glue between loom and ComfyUI.

### Acceptance Criteria

- [ ] `LoomComfyClient` class with `submit(workflow_json)`, `wait(job_id, timeout)`, `fetch_outputs(job_id, dest_dir)`
- [ ] Handles ComfyUI not running (connection refused) with a clear error
- [ ] Handles job failure (reports ComfyUI's error)
- [ ] Configurable timeout per job
- [ ] Logs submit / poll / complete events with job IDs
- [ ] Tests with respx or equivalent httpx mock
- [ ] All tests pass
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/comfy.py` | Create | Async client |
| `tests/test_comfy.py` | Create | Mocked HTTP tests |
| `docs/comfyui-setup.md` | Create | How to run ComfyUI so loom can reach it |

### Key Decisions

- httpx async. Matches morning-brief.
- Workflow JSONs in `workflows/`, parameterized at runtime.
- Borrow patterns from rbbrdckybk/comfy-batcher where useful.

### Notes

ComfyUI API endpoints: POST /prompt, GET /history/{id}, GET /view. Docs at https://docs.comfy.org. AI developer should verify current API shape before implementation.

---

## Feature: overlay-preprocess

**Branch:** `feature/overlay-preprocess`
**Depends on:** comfyui-client
**Status:** Merged
**Requires:** ai

### Goal

Preprocess overlay clips via ComfyUI into abstracted form (lineart, edges, depth) for compositing. No diffusion; cheap operations.

### Acceptance Criteria

- [ ] `loom preprocess --input overlay.mp4 --style lineart --output overlay.preprocessed.mp4` works
- [ ] Supports styles: `lineart`, `canny`, `depth`, `hed`
- [ ] Uses workflow JSONs in `workflows/preprocess/{style}.json`
- [ ] Output matches input length and fps
- [ ] Fails gracefully if ComfyUI is down or workflow errors
- [ ] All tests pass (mocked ComfyUI)
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/preprocess.py` | Create | Orchestrates ComfyUI calls per frame batch |
| `src/loom/cli.py` | Modify | Add `preprocess` subcommand |
| `src/loom/video_io.py` | Create | ffmpeg frame extract / encode helpers |
| `workflows/preprocess/lineart.json` | Create | Lineart extraction workflow |
| `workflows/preprocess/canny.json` | Create | Canny edge workflow |
| `workflows/preprocess/depth.json` | Create | Depth map workflow |
| `workflows/preprocess/hed.json` | Create | HED edge workflow |
| `tests/test_preprocess.py` | Create | Integration tests with mocked ComfyUI |
| `docs/preprocess-styles.md` | Create | Visual reference, when to use which |

### Key Decisions

- Frame-batch submission (default 16 frames per ComfyUI call) to amortize overhead.
- Video I/O via ffmpeg subprocess, not Python video libs.
- Workflow JSONs are assets, not code. Committed, not linted.

### Notes

Workflow JSONs are creative recipes. Expect iteration as user sees output.

---

## Feature: composite

**Branch:** `feature/composite`
**Depends on:** song-analysis, overlay-preprocess
**Status:** Merged
**Requires:** ai

### Goal

Composite preprocessed overlays onto base footage with blend modes and opacity modulated by song structure. Produces final visual track (no audio yet).

### Acceptance Criteria

- [ ] `loom composite --config project.toml` produces `output/composite.mp4` (no audio)
- [ ] Opacity curve driven by song structure JSON, configurable per section in TOML
- [ ] Supports blend modes: screen, add, multiply, overlay
- [ ] Overlay shorter than base: loops or holds (configurable)
- [ ] Overlay longer than base: trims
- [ ] Output resolution and fps match config
- [ ] All tests pass
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/composite.py` | Create | ffmpeg filter graph builder + runner |
| `src/loom/cli.py` | Modify | Add `composite` subcommand |
| `src/loom/timeline.py` | Create | Maps song structure → per-frame parameters |
| `tests/test_timeline.py` | Create | Timeline math unit tests |
| `tests/test_composite.py` | Create | Integration test with tiny clips |
| `docs/compositing.md` | Create | Blend mode reference, timeline config syntax |

### Key Decisions

- ffmpeg filter_complex for compositing. Not OpenCV.
- Timeline config in project TOML: `[[timeline]] section="chorus" opacity=0.7 blend="screen"`.

### Notes

ffmpeg blend filter docs worth referencing. Some modes produce unintuitive results; docs should include examples.

---

## Feature: diffusion-stylize

**Branch:** `feature/diffusion-stylize`
**Depends on:** comfyui-client
**Status:** Merged
**Requires:** ai

### Goal

Optional AnimateDiff + ControlNet pass that stylizes base footage per a text prompt. Runs before compositing if enabled.

### Acceptance Criteria

- [ ] `loom stylize --input base.mp4 --prompt "watercolor painting" --output stylized.mp4` works
- [ ] Uses `workflows/stylize/animatediff_controlnet.json`
- [ ] ControlNet input preserves motion from source (openpose, depth, or lineart chosen per workflow)
- [ ] Renders at 512px base resolution (6GB VRAM constraint)
- [ ] Output length matches input
- [ ] If OOM: fails cleanly with actionable error, does not hang
- [ ] Config flag `enable_diffusion = false` disables this feature entirely
- [ ] All tests pass (mocked ComfyUI)
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/stylize.py` | Create | AnimateDiff orchestration |
| `src/loom/cli.py` | Modify | Add `stylize` subcommand |
| `workflows/stylize/animatediff_controlnet.json` | Create | AnimateDiff + ControlNet workflow |
| `tests/test_stylize.py` | Create | Tests with mocked ComfyUI |
| `docs/stylization.md` | Create | Prompt guide, VRAM considerations, OOM troubleshooting |

### Key Decisions

- SD1.5-based AnimateDiff, not newer models (6GB VRAM).
- Frame-batch via AnimateDiff motion module; not frame-by-frame.
- OOM handling: loom detects ComfyUI OOM response, logs actionable error, skips feature rather than looping.

### Notes

This is the feature most likely to fail on 6GB. Acceptance criteria explicitly require graceful OOM handling. ComfyUI's smart offloading may help. Upscaling stylized output to 1080p is done in the pipeline-runner feature via Real-ESRGAN.

---

## Feature: upscale

**Branch:** `feature/upscale`
**Depends on:** comfyui-client
**Status:** In Progress
**Requires:** ai

### Goal

Upscale 512px renders to 1080p via Real-ESRGAN in ComfyUI. Runs after compositing (and stylization, if used) before final mux.

### Acceptance Criteria

- [ ] `loom upscale --input low.mp4 --output high.mp4 --target 1080p` works
- [ ] Uses `workflows/upscale/real_esrgan.json`
- [ ] Preserves fps and audio (though audio passes through separately in pipeline)
- [ ] Handles arbitrary input resolutions, targets 1080p or 4K
- [ ] All tests pass (mocked ComfyUI)
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/upscale.py` | Create | Real-ESRGAN orchestration |
| `src/loom/cli.py` | Modify | Add `upscale` subcommand |
| `workflows/upscale/real_esrgan.json` | Create | Real-ESRGAN workflow |
| `tests/test_upscale.py` | Create | Tests with mocked ComfyUI |

### Key Decisions

- Real-ESRGAN 2x or 4x models, chosen by target resolution.
- Runs per-frame on GPU; fits in 6GB easily.

---

## Feature: pipeline-runner

**Branch:** `feature/pipeline-runner`
**Depends on:** composite, diffusion-stylize, upscale
**Status:** Not Started
**Requires:** ai

### Goal

End-to-end `loom run` orchestrating: analyze → (stylize) → preprocess overlays → composite → upscale → mux audio → final MP4. Idempotent, resumable, respects wall-clock deadline.

### Acceptance Criteria

- [ ] `loom run --config project.toml` produces `output/final.mp4` with audio
- [ ] `--deadline 05:30` stops cleanly at that time, leaves resumable state
- [ ] Re-running after interrupt skips completed stages
- [ ] `--force` re-runs all stages
- [ ] Progress logged to `output/loom.log` and stdout
- [ ] Exit code 0 on success, non-zero with clear message on failure
- [ ] Failure in one stage does not trigger infinite retry loop (max 2 retries per stage)
- [ ] All tests pass
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/pipeline.py` | Create | Stage orchestration, deadline handling, retry limits |
| `src/loom/state.py` | Create | Persistent state file |
| `src/loom/cli.py` | Modify | Wire `run` to pipeline |
| `tests/test_pipeline.py` | Create | End-to-end tests with tiny fixtures |
| `docs/pipeline.md` | Create | Stage diagram, resume semantics, failure handling |

### Key Decisions

- State file: `output/.loom-state.json`. Stages marked completed with input hashes.
- Deadline check between stages. A single render may exceed deadline if already started.
- Max 2 retries per stage before marking failed and exiting.
- ffmpeg for final audio mux, no video re-encode.

### Notes

Resumability matters: a 4-hour window may not fit a full render. Three nights to completion is acceptable.

---

## Feature: systemd-integration

**Branch:** `feature/systemd-integration`
**Depends on:** pipeline-runner
**Status:** Not Started
**Requires:** both

### Goal

Scheduled nightly execution via systemd user timer in the 00:00–05:30 window, with Ollama stopped before and restarted after.

### Acceptance Criteria

- [HUMAN] Systemd unit installed to `~/.config/systemd/user/`
- [HUMAN] Timer enabled and triggers at 00:00 local
- [HUMAN] Unit stops `ollama.service` before loom, restarts at 05:30 or on loom exit
- [HUMAN] Morning-brief timer moved from 04:15 to 05:30 (one-line change in morning-brief's timer file)
- [ ] `loom install-systemd --project-dir ...` generates unit files for user to install
- [ ] Generated units use correct paths, deadline, log location
- [ ] `loom run` respects `LOOM_DEADLINE` env var
- [ ] `docs/scheduling.md` explains install, verification, troubleshooting
- [ ] Lint clean

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/loom/systemd.py` | Create | Unit file templating |
| `src/loom/cli.py` | Modify | Add `install-systemd` subcommand |
| `templates/loom.service.j2` | Create | Service unit template |
| `templates/loom.timer.j2` | Create | Timer template |
| `docs/scheduling.md` | Create | Install guide |
| `tests/test_systemd.py` | Create | Template rendering tests |

### Key Decisions

- Ollama stop/start via `ExecStartPre` / `ExecStopPost` in the service unit. Ad-hoc for now; dtl may generalize later.
- User manually activates units. Loom generates only.

### Notes

Morning-brief's 04:15 → 05:30 move is a `Requires: both` task because it touches a different project. Flag this to PM clearly.

---

## Feature: docs-and-readme

**Branch:** `feature/docs-and-readme`
**Depends on:** systemd-integration
**Status:** Not Started
**Requires:** ai

### Goal

Final README and docs consolidation. Walks a new user from zero to first rendered video.

### Acceptance Criteria

- [ ] `README.md` covers pitch, hardware requirements, dependencies, quick start, project layout, first render walkthrough, scheduling, troubleshooting
- [ ] All `docs/*.md` cross-link correctly
- [ ] `docs/first-project.md` walks through building the palm-tree demo end-to-end
- [ ] All internal links verified
- [ ] Lint clean (no tests this feature)

### Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `README.md` | Modify | Full rewrite from stub |
| `docs/first-project.md` | Create | Palm-tree demo walkthrough |
| `docs/troubleshooting.md` | Create | Common failures |
| `docs/hardware.md` | Create | Why 6GB shapes things |

### Notes

Write after everything else exists, so it reflects reality.
