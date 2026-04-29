"""AnimateDiff + ControlNet stylization via ComfyUI.

Runs an optional img2img pass over base footage using an SD1.5-based
AnimateDiff motion module and a lineart ControlNet.  Source frames are
submitted in batches (the AnimateDiff context window) to preserve motion
coherence.  All processing is at 512px to fit within a 6 GB VRAM budget.

OOM handling: ComfyUI reports CUDA out-of-memory as a job execution error.
This module detects that signal and raises :class:`OOMError` with an
actionable message rather than retrying or hanging.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loom.comfy import ComfyError, ComfyJobError, LoomComfyClient
from loom.video_io import VideoIOError, encode_frames, extract_frames

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_LENGTH: int = 16
DEFAULT_NEGATIVE_PROMPT: str = (
    "blurry, low quality, deformed, ugly, bad anatomy, watermark, text, signature"
)

# Resolved at import time; works from installed package or editable install.
_WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "workflows" / "stylize"

# Keywords in ComfyUI execution-error messages that indicate GPU OOM.
_OOM_MARKERS = ("out of memory", "cuda", "outofmemoryerror", "cudaoutofmemory")


class StylizeError(Exception):
    """Raised when stylization fails."""


class OOMError(StylizeError):
    """Raised when ComfyUI reports a GPU out-of-memory error.

    Callers should not retry; the user must reduce context_length, batch_size,
    or resolution, or enable ComfyUI smart memory offloading.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def stylize_video(
    input_path: Path,
    output_path: Path,
    prompt: str,
    *,
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
    comfy_url: str = "http://127.0.0.1:8188",
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    cfg: float = 7.0,
    seed: int = 42,
    workflows_dir: Path | None = None,
) -> None:
    """Stylize *input_path* with AnimateDiff + ControlNet and write *output_path*.

    Parameters
    ----------
    input_path:
        Source video file (base footage).
    output_path:
        Destination for the stylized video.
    prompt:
        Positive text prompt describing the desired visual style.
    negative_prompt:
        Negative text prompt; defaults to a generic quality filter.
    comfy_url:
        Base URL of the running ComfyUI instance.
    context_length:
        Number of frames per AnimateDiff context window.  Must be ≤ 16 on
        a 6 GB GPU.  Reducing this lowers VRAM use but may reduce motion
        coherence.
    width:
        Output width in pixels.  Keep at 512 for 6 GB VRAM budget.
    height:
        Output height in pixels.  Keep at 512 for 6 GB VRAM budget.
    steps:
        KSampler denoising steps.
    cfg:
        Classifier-free guidance scale.
    seed:
        RNG seed for reproducibility.
    workflows_dir:
        Directory containing ``animatediff_controlnet.json``.
        Defaults to the repo's ``workflows/stylize/`` directory.

    Raises
    ------
    StylizeError
        On any ffmpeg or ComfyUI failure.
    OOMError
        If ComfyUI reports a GPU out-of-memory error.  The caller must not
        retry automatically.
    """
    wf_dir = workflows_dir or _WORKFLOWS_DIR
    workflow_path = wf_dir / "animatediff_controlnet.json"
    if not workflow_path.exists():
        raise StylizeError(f"Workflow file not found: {workflow_path}")

    workflow_template: dict[str, Any] = json.loads(workflow_path.read_text())
    logger.info(
        "stylize: input=%s output=%s prompt=%r context_length=%d",
        input_path,
        output_path,
        prompt,
        context_length,
    )

    with tempfile.TemporaryDirectory(prefix="loom_stylize_") as tmpdir:
        tmp = Path(tmpdir)
        frames_in = tmp / "frames_in"
        frames_out = tmp / "frames_out"
        frames_out.mkdir()

        try:
            frames, fps = extract_frames(input_path, frames_in)
        except VideoIOError as exc:
            raise StylizeError(f"Frame extraction failed: {exc}") from exc

        if not frames:
            raise StylizeError(f"No frames extracted from {input_path}")

        logger.info("Stylizing %d frames in context windows of %d", len(frames), context_length)

        try:
            async with LoomComfyClient(comfy_url) as client:
                frame_idx = 0
                for batch_start in range(0, len(frames), context_length):
                    batch = frames[batch_start : batch_start + context_length]
                    logger.info(
                        "Submitting batch frames %d-%d / %d",
                        batch_start + 1,
                        batch_start + len(batch),
                        len(frames),
                    )
                    await _process_batch(
                        client,
                        batch,
                        frame_idx,
                        workflow_template,
                        frames_out,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width=width,
                        height=height,
                        steps=steps,
                        cfg=cfg,
                        seed=seed,
                    )
                    frame_idx += len(batch)
        except OOMError:
            raise
        except ComfyError as exc:
            raise StylizeError(f"ComfyUI error: {exc}") from exc

        try:
            encode_frames(frames_out, output_path, fps)
        except VideoIOError as exc:
            raise StylizeError(f"Frame encoding failed: {exc}") from exc

    logger.info("stylize complete: %s", output_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _process_batch(
    client: LoomComfyClient,
    frames: list[Path],
    frame_offset: int,
    workflow_template: dict[str, Any],
    out_dir: Path,
    *,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> None:
    """Upload *frames*, submit AnimateDiff workflow, and save output frames.

    Output files are named ``frame_{frame_offset+i+1:06d}.png`` in *out_dir*.
    """
    # Upload all frames in this batch.
    uploaded: list[str] = []
    for frame in frames:
        comfy_name = await client.upload_image(frame)
        uploaded.append(comfy_name)

    workflow = _build_workflow(
        workflow_template,
        uploaded,
        prompt,
        negative_prompt,
        width,
        height,
        steps,
        cfg,
        seed,
    )

    job_id = await client.submit(workflow)
    try:
        await client.wait(job_id)
    except ComfyJobError as exc:
        _check_oom(exc)
        raise

    fetch_dir = out_dir / f".fetch_{frame_offset:06d}"
    try:
        downloaded = await client.fetch_outputs(job_id, fetch_dir)
        if not downloaded:
            raise StylizeError(
                f"No output files returned for batch starting at frame {frame_offset + 1}"
            )

        # ComfyUI SaveImage names outputs sequentially when given a batch tensor.
        # Sort to ensure stable ordering.
        downloaded_sorted = sorted(downloaded, key=lambda p: p.name)
        for i, src in enumerate(downloaded_sorted):
            dest = out_dir / f"frame_{frame_offset + i + 1:06d}.png"
            shutil.copy2(src, dest)
            logger.debug("Saved stylized frame: %s", dest.name)

        if len(downloaded_sorted) != len(frames):
            logger.warning(
                "Expected %d output frame(s) for batch, got %d — "
                "output length may not match input.",
                len(frames),
                len(downloaded_sorted),
            )
    finally:
        if fetch_dir.exists():
            shutil.rmtree(fetch_dir)


def _build_workflow(
    template: dict[str, Any],
    frame_filenames: list[str],
    positive_prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> dict[str, Any]:
    """Return a ComfyUI workflow dict for the given batch of frames.

    Replaces the ``loom_batch_input`` stub LoadImage node with N LoadImage
    nodes (one per frame) connected through ImageBatch nodes.  Injects text
    prompts, resolution, and sampler parameters.

    Raises
    ------
    StylizeError
        If the workflow template has no ``loom_batch_input`` LoadImage node.
    """
    wf = copy.deepcopy(template)

    # Locate the stub LoadImage node.
    stub_id: str | None = None
    for nid, node in wf.items():
        if (
            isinstance(node, dict)
            and node.get("class_type") == "LoadImage"
            and node.get("inputs", {}).get("image") == "loom_batch_input"
        ):
            stub_id = nid
            break

    if stub_id is None:
        raise StylizeError(
            "Workflow template missing a LoadImage node with image='loom_batch_input'"
        )

    # Build the batch-loading subgraph.
    if len(frame_filenames) == 1:
        # Single frame: just replace the stub in-place.
        wf[stub_id]["inputs"]["image"] = frame_filenames[0]
        final_batch_id = stub_id
    else:
        # Remove stub; add N LoadImage nodes + (N-1) ImageBatch nodes.
        del wf[stub_id]

        # Use high node IDs to avoid collisions with template node IDs.
        base_id = 1000
        load_ids: list[str] = []
        for i, fname in enumerate(frame_filenames):
            nid = str(base_id + i)
            wf[nid] = {
                "class_type": "LoadImage",
                "inputs": {"image": fname, "upload": "image"},
            }
            load_ids.append(nid)

        # Chain LoadImage outputs through ImageBatch nodes.
        batch_counter = base_id + len(frame_filenames)
        prev_batch_id = load_ids[0]
        for i in range(1, len(load_ids)):
            nid = str(batch_counter)
            wf[nid] = {
                "class_type": "ImageBatch",
                "inputs": {
                    "image1": [prev_batch_id, 0],
                    "image2": [load_ids[i], 0],
                },
            }
            prev_batch_id = nid
            batch_counter += 1

        final_batch_id = prev_batch_id

        # Rewire any node that referenced the stub to the final ImageBatch.
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            for key, val in node.get("inputs", {}).items():
                if isinstance(val, list) and len(val) == 2 and val[0] == stub_id:
                    node["inputs"][key] = [final_batch_id, val[1]]

    # Inject text prompts.
    for node in wf.values():
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            continue
        text = node.get("inputs", {}).get("text", "")
        if text == "loom_positive_prompt":
            node["inputs"]["text"] = positive_prompt
        elif text == "loom_negative_prompt":
            node["inputs"]["text"] = negative_prompt

    # Inject resolution into preprocessor nodes.
    resolution = min(width, height)
    for node in wf.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in (
            "LineArtPreprocessor",
            "CannyEdgePreprocessor",
            "HEDPreprocessor",
            "MiDaSDepthMapPreprocessor",
        ):
            node["inputs"]["resolution"] = resolution

    # Inject KSampler parameters.
    for node in wf.values():
        if not isinstance(node, dict) or node.get("class_type") != "KSampler":
            continue
        node["inputs"]["steps"] = steps
        node["inputs"]["cfg"] = cfg
        node["inputs"]["seed"] = seed

    return wf


def _check_oom(exc: ComfyJobError) -> None:
    """Raise :class:`OOMError` if *exc* looks like a GPU out-of-memory error."""
    msg_lower = str(exc).lower()
    if any(marker in msg_lower for marker in _OOM_MARKERS):
        raise OOMError(
            f"ComfyUI ran out of GPU memory (job {exc.job_id}). "
            "To recover: reduce --context-length (try 8), lower --width/--height, "
            "or enable ComfyUI smart memory offloading "
            "(--lowvram / --novram launch flags)."
        ) from exc
