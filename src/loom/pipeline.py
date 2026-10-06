"""End-to-end pipeline orchestration.

Stages (in order):
  1. analyze     — librosa song-structure JSON
  2. stylize     — AnimateDiff + ControlNet (optional, enable_diffusion=true)
  3. preprocess  — ComfyUI lineart/canny/depth/hed on each overlay
  4. composite   — ffmpeg filter_complex blend
  5. upscale     — Real-ESRGAN to 1080p
  6. mux         — ffmpeg audio mux → final.mp4

Resumability: a JSON state file tracks which stages completed.  Re-running
skips completed stages unless --force is given.  The LOOM_DEADLINE env var
(or config schedule.deadline) is checked between stages; if the clock has
passed the deadline the pipeline exits cleanly with a DeadlineReached error
and can be resumed the next night.

Max 2 retries per stage.  GPU OOM errors are never retried.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, time
from pathlib import Path

from loom.analysis import analyze_song
from loom.composite import run_composite
from loom.config import LoomConfig
from loom.state import PipelineState, file_hash

logger = logging.getLogger(__name__)

MAX_RETRIES: int = 2


class PipelineError(Exception):
    """Raised when a pipeline stage fails after all retries."""


class DeadlineReached(Exception):
    """Raised when the wall-clock deadline is reached between stages."""


# ---------------------------------------------------------------------------
# Deadline helpers
# ---------------------------------------------------------------------------


def _parse_deadline(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _deadline_reached(deadline: time) -> bool:
    return datetime.now().time() >= deadline


def _check_deadline(deadline: time, next_stage: str) -> None:
    if _deadline_reached(deadline):
        raise DeadlineReached(
            f"Deadline {deadline.strftime('%H:%M')} reached before stage {next_stage!r}. "
            "Re-run to resume from the last completed stage."
        )


# ---------------------------------------------------------------------------
# Stage runner with retry logic
# ---------------------------------------------------------------------------


def _is_oom(exc: Exception) -> bool:
    """Return True if *exc* is any loom OOMError (upscale or stylize)."""
    return type(exc).__name__ == "OOMError"


def _run_stage(
    name: str,
    fn,
    *args,
    state: PipelineState,
    input_hash: str | None = None,
    **kwargs,
):
    """Call ``fn(*args, **kwargs)`` with up to MAX_RETRIES retries.

    On success: marks *name* complete in *state* and returns the result.
    On OOM:     marks failed and raises PipelineError immediately (no retry).
    On failure: logs a warning and retries.  After MAX_RETRIES exhausted,
                marks failed and raises PipelineError.
    """
    last_exc: Exception | None = None
    total = MAX_RETRIES + 1  # 1 attempt + MAX_RETRIES retries
    for attempt in range(total):
        try:
            result = fn(*args, **kwargs)
            state.mark_complete(name, input_hash)
            return result
        except Exception as exc:  # noqa: BLE001
            if _is_oom(exc):
                state.mark_failed(name)
                raise PipelineError(
                    f"Stage {name!r} failed (GPU OOM — non-retriable): {exc}"
                ) from exc
            last_exc = exc
            if attempt < MAX_RETRIES:
                logger.warning(
                    "Stage %r attempt %d/%d failed: %s — retrying",
                    name,
                    attempt + 1,
                    total,
                    exc,
                )

    state.mark_failed(name)
    raise PipelineError(f"Stage {name!r} failed after {total} attempt(s): {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Mux helper
# ---------------------------------------------------------------------------


def _mux_audio(video: Path, audio: Path, output: Path) -> None:
    """Mux *audio* into *video* with ffmpeg (copy video stream, encode audio AAC)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise PipelineError(
            f"ffmpeg mux failed (exit {result.returncode}):\n"
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    logger.info("mux: written %s", output)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(
    cfg: LoomConfig,
    *,
    force: bool = False,
    comfy_url: str = "http://127.0.0.1:8188",
) -> Path:
    """Orchestrate the full render pipeline and return the path to ``final.mp4``.

    Parameters
    ----------
    cfg:
        Loaded and validated project config.
    force:
        Re-run all stages even if state marks them complete.
    comfy_url:
        Base URL of the running ComfyUI instance.

    Raises
    ------
    PipelineError
        If a stage fails after all retries.
    DeadlineReached
        If the wall-clock deadline passes between stages.
    """
    deadline_str = os.environ.get("LOOM_DEADLINE", cfg.schedule.deadline)
    deadline = _parse_deadline(deadline_str)

    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    state = PipelineState(output_dir)
    if force:
        logger.info("--force: clearing all pipeline state")
        state.clear()

    # ------------------------------------------------------------------ #
    # Stage 1: Analyze                                                     #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "analyze")
    song_hash = file_hash(cfg.paths.song) if cfg.paths.song.exists() else None
    if state.is_complete("analyze", song_hash):
        logger.info("Stage analyze: skipping (already complete)")
    else:
        logger.info("Stage analyze: running")
        _run_stage("analyze", analyze_song, cfg.paths.song, state=state, input_hash=song_hash)

    # ------------------------------------------------------------------ #
    # Stage 2: Stylize (optional)                                          #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "stylize")
    stylize_out = output_dir / "stylized.mp4"
    if cfg.render.enable_diffusion:
        base_hash = file_hash(cfg.paths.base) if cfg.paths.base.exists() else None
        if state.is_complete("stylize", base_hash):
            logger.info("Stage stylize: skipping (already complete)")
        else:
            logger.info("Stage stylize: running")
            from loom.stylize import stylize_video  # noqa: PLC0415

            _run_stage(
                "stylize",
                lambda: asyncio.run(  # noqa: PLC3002
                    stylize_video(
                        cfg.paths.base,
                        stylize_out,
                        "cinematic stylized music video",
                        comfy_url=comfy_url,
                    )
                ),
                state=state,
                input_hash=base_hash,
            )
        # Redirect base footage for composite to the stylized output
        if stylize_out.exists():
            cfg.paths.base = stylize_out
    else:
        logger.info("Stage stylize: skipped (enable_diffusion=false)")

    # ------------------------------------------------------------------ #
    # Stage 3: Preprocess overlays                                         #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "preprocess")
    if cfg.paths.overlays:
        from loom.preprocess import preprocess_overlay  # noqa: PLC0415

        preproc_paths: list[Path] = []
        for ovl in cfg.paths.overlays:
            stage_key = f"preprocess_{ovl.stem}"
            ovl_hash = file_hash(ovl) if ovl.exists() else None
            preproc_out = output_dir / f"preproc_{ovl.stem}.mp4"
            if state.is_complete(stage_key, ovl_hash):
                logger.info("Stage %r: skipping (already complete)", stage_key)
            else:
                logger.info("Stage %r: running", stage_key)
                _run_stage(
                    stage_key,
                    lambda o=ovl, p=preproc_out: asyncio.run(  # noqa: PLC3002
                        preprocess_overlay(o, p, "lineart", comfy_url=comfy_url)
                    ),
                    state=state,
                    input_hash=ovl_hash,
                )
            preproc_paths.append(preproc_out)
        # Replace original overlay paths with preprocessed versions
        cfg.paths.overlays = [p for p in preproc_paths if p.exists()]

    # ------------------------------------------------------------------ #
    # Stage 4: Composite                                                   #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "composite")
    if state.is_complete("composite"):
        logger.info("Stage composite: skipping (already complete)")
    else:
        logger.info("Stage composite: running")
        _run_stage("composite", run_composite, cfg, state=state)

    composite_out = output_dir / "composite.mp4"

    # ------------------------------------------------------------------ #
    # Stage 5: Upscale                                                     #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "upscale")
    upscale_out = output_dir / "upscaled.mp4"
    if state.is_complete("upscale"):
        logger.info("Stage upscale: skipping (already complete)")
    else:
        logger.info("Stage upscale: running")
        from loom.upscale import upscale_video  # noqa: PLC0415

        _run_stage(
            "upscale",
            lambda: asyncio.run(  # noqa: PLC3002
                upscale_video(composite_out, upscale_out, "1080p", comfy_url=comfy_url)
            ),
            state=state,
        )

    # ------------------------------------------------------------------ #
    # Stage 6: Mux audio                                                   #
    # ------------------------------------------------------------------ #
    _check_deadline(deadline, "mux")
    final_out = output_dir / "final.mp4"
    if state.is_complete("mux"):
        logger.info("Stage mux: skipping (already complete)")
    else:
        logger.info("Stage mux: running")
        _run_stage("mux", _mux_audio, upscale_out, cfg.paths.song, final_out, state=state)

    logger.info("Pipeline complete: %s", final_out)
    return final_out
