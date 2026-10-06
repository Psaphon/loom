# Pipeline

## Overview

`loom run` orchestrates the full render pipeline end-to-end:

```
analyze → [stylize] → preprocess → composite → upscale → mux → final.mp4
```

Each stage is idempotent: completed stages are recorded in `output/.loom-state.json` and skipped on the next run unless `--force` is given.

## Stages

| # | Name | Module | Output | Skippable |
|---|------|--------|--------|-----------|
| 1 | analyze | `analysis.py` | `{song}.structure.json` | Yes (hash-matched) |
| 2 | stylize | `stylize.py` | `output/stylized.mp4` | Yes (hash-matched); skipped when `enable_diffusion=false` |
| 3 | preprocess | `preprocess.py` | `output/preproc_{stem}.mp4` (per overlay) | Yes (hash-matched) |
| 4 | composite | `composite.py` | `output/composite.mp4` | Yes |
| 5 | upscale | `upscale.py` | `output/upscaled.mp4` | Yes |
| 6 | mux | `pipeline.py` | `output/final.mp4` | Yes |

## Usage

```bash
loom run --config project.toml
loom run --config project.toml --force          # re-run all stages
loom run --config project.toml --deadline 05:30  # override deadline
loom run --config project.toml --dry-run         # validate config only
```

The `LOOM_DEADLINE` and `COMFY_URL` environment variables are also respected.

## Resume Semantics

The state file `output/.loom-state.json` records each stage's completion status and, where applicable, a SHA-256 fingerprint of the primary input file (first 64 KiB). On the next run:

- A stage whose status is `"completed"` **and** whose input hash matches is skipped.
- If the input hash has changed (e.g. the song file was replaced) the stage re-runs.
- Deleting the state file or passing `--force` causes all stages to re-run.

A single pipeline run may not complete within the nightly 05:30 window. Three-night completion is acceptable: the pipeline exits cleanly when the deadline is reached between stages and resumes where it left off the following night.

## Deadline Handling

The deadline is checked **between** stages, not during them. A stage that has already started will run to completion even if it overruns the deadline. The next stage check will then raise `DeadlineReached` and the process exits with code `2`.

Deadline resolution order:

1. `--deadline` CLI flag
2. `LOOM_DEADLINE` environment variable
3. `schedule.deadline` in `project.toml` (default `"05:30"`)

## Failure Handling

Each stage gets **at most MAX_RETRIES=2 retries** (3 total attempts) before the pipeline exits with a non-zero code.

- **GPU OOM** (`OOMError`) is non-retriable — the pipeline stops immediately with exit code 1. Enable ComfyUI `--lowvram` / `--novram` to recover.
- **Transient errors** (network, subprocess) are retried with a warning log per attempt.
- On final failure the stage is recorded as `"failed"` in the state file (informational; it does not block re-runs).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — `output/final.mp4` produced |
| 1 | Pipeline error — see log for details |
| 2 | Deadline reached — re-run to resume |

## Logging

Progress is written to both stdout and `output/loom.log` (appended on each run). Use `--debug` for verbose output.

## State File Schema

```json
{
  "stages": {
    "analyze": { "status": "completed", "input_hash": "<sha256>" },
    "stylize": { "status": "completed", "input_hash": "<sha256>" },
    "preprocess_overlay1": { "status": "completed", "input_hash": "<sha256>" },
    "composite": { "status": "completed", "input_hash": null },
    "upscale": { "status": "completed", "input_hash": null },
    "mux": { "status": "completed", "input_hash": null }
  }
}
```

`status` is `"completed"` or `"failed"`. The file is safe to delete and hand-edit.
