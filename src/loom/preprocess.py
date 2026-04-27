"""Overlay preprocessing via ComfyUI (lineart, canny, depth, hed).

Each overlay clip is extracted to frames, run through a ComfyUI preprocessor
workflow in concurrent batches, then re-encoded to video.  No diffusion —
these are cheap deterministic operations.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loom.comfy import ComfyError, LoomComfyClient
from loom.video_io import VideoIOError, encode_frames, extract_frames

logger = logging.getLogger(__name__)

VALID_STYLES: frozenset[str] = frozenset({"lineart", "canny", "depth", "hed"})
DEFAULT_BATCH_SIZE: int = 16

# Resolved at import time; works from installed package or editable install.
_WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "workflows" / "preprocess"


class PreprocessError(Exception):
    """Raised when overlay preprocessing fails."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def preprocess_overlay(
    input_path: Path,
    output_path: Path,
    style: str,
    *,
    comfy_url: str = "http://127.0.0.1:8188",
    batch_size: int = DEFAULT_BATCH_SIZE,
    workflows_dir: Path | None = None,
) -> None:
    """Preprocess *input_path* with *style* via ComfyUI and write *output_path*.

    Parameters
    ----------
    input_path:
        Source overlay video file.
    output_path:
        Destination for the preprocessed video.
    style:
        One of ``"lineart"``, ``"canny"``, ``"depth"``, ``"hed"``.
    comfy_url:
        Base URL of the running ComfyUI instance.
    batch_size:
        Number of frames to submit to ComfyUI concurrently per round.
    workflows_dir:
        Directory containing ``{style}.json`` workflow files.
        Defaults to the repo's ``workflows/preprocess/`` directory.

    Raises
    ------
    ValueError
        If *style* is not a known style.
    PreprocessError
        On any ffmpeg or ComfyUI failure.
    """
    if style not in VALID_STYLES:
        raise ValueError(f"Unknown style: {style!r}. Valid styles: {sorted(VALID_STYLES)}")

    wf_dir = workflows_dir or _WORKFLOWS_DIR
    workflow_path = wf_dir / f"{style}.json"
    if not workflow_path.exists():
        raise PreprocessError(f"Workflow file not found: {workflow_path}")

    workflow_template: dict[str, Any] = json.loads(workflow_path.read_text())
    logger.info("preprocess: style=%s input=%s output=%s", style, input_path, output_path)

    with tempfile.TemporaryDirectory(prefix="loom_preprocess_") as tmpdir:
        tmp = Path(tmpdir)
        frames_in = tmp / "frames_in"
        frames_out = tmp / "frames_out"
        frames_out.mkdir()

        try:
            frames, fps = extract_frames(input_path, frames_in)
        except VideoIOError as exc:
            raise PreprocessError(f"Frame extraction failed: {exc}") from exc

        if not frames:
            raise PreprocessError(f"No frames extracted from {input_path}")

        logger.info("Processing %d frames in batches of %d", len(frames), batch_size)

        try:
            async with LoomComfyClient(comfy_url) as client:
                for batch_start in range(0, len(frames), batch_size):
                    batch = frames[batch_start : batch_start + batch_size]
                    logger.info(
                        "Submitting batch frames %d-%d / %d",
                        batch_start + 1,
                        batch_start + len(batch),
                        len(frames),
                    )
                    tasks = [
                        _process_frame(
                            client,
                            frame,
                            batch_start + i,
                            workflow_template,
                            frames_out,
                        )
                        for i, frame in enumerate(batch)
                    ]
                    await asyncio.gather(*tasks)
        except ComfyError as exc:
            raise PreprocessError(f"ComfyUI error: {exc}") from exc

        try:
            encode_frames(frames_out, output_path, fps)
        except VideoIOError as exc:
            raise PreprocessError(f"Frame encoding failed: {exc}") from exc

    logger.info("preprocess complete: %s", output_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _process_frame(
    client: LoomComfyClient,
    frame: Path,
    frame_idx: int,
    workflow_template: dict[str, Any],
    out_dir: Path,
) -> None:
    """Upload, process via ComfyUI, and save a single frame.

    Output is written as ``frame_{frame_idx+1:06d}.png`` in *out_dir*.
    """
    comfy_filename = await client.upload_image(frame)
    workflow = _inject_input_image(workflow_template, comfy_filename)

    job_id = await client.submit(workflow)
    await client.wait(job_id)

    fetch_dir = out_dir / f".fetch_{frame_idx:06d}"
    try:
        downloaded = await client.fetch_outputs(job_id, fetch_dir)
        if not downloaded:
            raise PreprocessError(f"No output files returned for frame {frame_idx + 1}")
        dest = out_dir / f"frame_{frame_idx + 1:06d}.png"
        shutil.copy2(downloaded[0], dest)
        logger.debug("Saved preprocessed frame: %s", dest.name)
    finally:
        if fetch_dir.exists():
            shutil.rmtree(fetch_dir)


def _inject_input_image(workflow: dict[str, Any], filename: str) -> dict[str, Any]:
    """Return a deep copy of *workflow* with the LoadImage node's image set to *filename*.

    Raises
    ------
    PreprocessError
        If the workflow contains no LoadImage node.
    """
    wf = copy.deepcopy(workflow)
    for node in wf.values():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            node["inputs"]["image"] = filename
            return wf
    raise PreprocessError("Workflow has no LoadImage node — cannot inject input frame filename")
