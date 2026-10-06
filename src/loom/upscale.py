"""Real-ESRGAN upscaling via ComfyUI.

Upscales low-resolution renders to 1080p or 4K by running the Real-ESRGAN
model through ComfyUI on a per-frame basis.  Model (2× or 4×) is chosen
automatically based on the ratio between the input resolution and the target.

OOM handling: ComfyUI reports CUDA out-of-memory as a job execution error.
This module detects that signal and raises :class:`OOMError` with an
actionable message rather than retrying or hanging.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loom.comfy import ComfyError, ComfyJobError, LoomComfyClient
from loom.video_io import VideoIOError, extract_frames

logger = logging.getLogger(__name__)

# Resolved at import time; works from installed package or editable install.
_WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "workflows" / "upscale"

_TARGETS: dict[str, tuple[int, int]] = {
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}

_MODEL_2X = "RealESRGAN_x2plus.pth"
_MODEL_4X = "RealESRGAN_x4plus.pth"

# Keywords in ComfyUI execution-error messages that indicate GPU OOM.
_OOM_MARKERS = ("out of memory", "cuda", "outofmemoryerror", "cudaoutofmemory")


class UpscaleError(Exception):
    """Raised when upscaling fails."""


class OOMError(UpscaleError):
    """Raised when ComfyUI reports a GPU out-of-memory error.

    Callers should not retry; the user must enable ComfyUI smart memory
    offloading (--lowvram / --novram launch flags).
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def upscale_video(
    input_path: Path,
    output_path: Path,
    target: str = "1080p",
    *,
    comfy_url: str = "http://127.0.0.1:8188",
    workflows_dir: Path | None = None,
) -> None:
    """Upscale *input_path* to *target* resolution via Real-ESRGAN in ComfyUI.

    Parameters
    ----------
    input_path:
        Source video file (typically a 512-px composite render).
    output_path:
        Destination for the upscaled video.
    target:
        Target resolution string: ``"1080p"`` (1920×1080) or ``"4k"`` (3840×2160).
    comfy_url:
        Base URL of the running ComfyUI instance.
    workflows_dir:
        Directory containing ``real_esrgan.json``.
        Defaults to the repo's ``workflows/upscale/`` directory.

    Raises
    ------
    UpscaleError
        On any ffmpeg or ComfyUI failure.
    OOMError
        If ComfyUI reports a GPU out-of-memory error.  The caller must not
        retry automatically.
    ValueError
        If *target* is not a recognised resolution string.
    """
    if target not in _TARGETS:
        raise ValueError(f"Unknown target resolution {target!r}; choices: {sorted(_TARGETS)}")

    target_w, target_h = _TARGETS[target]

    wf_dir = workflows_dir or _WORKFLOWS_DIR
    workflow_path = wf_dir / "real_esrgan.json"
    if not workflow_path.exists():
        raise UpscaleError(f"Workflow file not found: {workflow_path}")

    workflow_template: dict[str, Any] = json.loads(workflow_path.read_text())

    logger.info("upscale: input=%s output=%s target=%s", input_path, output_path, target)

    try:
        input_w, input_h = _get_video_resolution(input_path)
    except UpscaleError as exc:
        raise UpscaleError(f"Could not read input resolution: {exc}") from exc

    model_name = _choose_model(input_w, input_h, target_w, target_h)
    logger.info(
        "Input resolution %dx%d → model=%s → output %dx%d",
        input_w,
        input_h,
        model_name,
        target_w,
        target_h,
    )

    with tempfile.TemporaryDirectory(prefix="loom_upscale_") as tmpdir:
        tmp = Path(tmpdir)
        frames_in = tmp / "frames_in"
        frames_out = tmp / "frames_out"
        frames_out.mkdir()

        try:
            frames, fps = extract_frames(input_path, frames_in)
        except VideoIOError as exc:
            raise UpscaleError(f"Frame extraction failed: {exc}") from exc

        if not frames:
            raise UpscaleError(f"No frames extracted from {input_path}")

        logger.info("Upscaling %d frames with %s", len(frames), model_name)

        try:
            async with LoomComfyClient(comfy_url) as client:
                for i, frame in enumerate(frames):
                    frame_num = i + 1
                    logger.info("Upscaling frame %d / %d", frame_num, len(frames))
                    await _upscale_frame(
                        client,
                        frame,
                        frame_num,
                        workflow_template,
                        frames_out,
                        model_name=model_name,
                    )
        except OOMError:
            raise
        except ComfyError as exc:
            raise UpscaleError(f"ComfyUI error: {exc}") from exc

        _encode_frames_scaled(frames_out, output_path, fps, target_w, target_h)

    logger.info("upscale complete: %s", output_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _upscale_frame(
    client: LoomComfyClient,
    frame: Path,
    frame_num: int,
    workflow_template: dict[str, Any],
    out_dir: Path,
    *,
    model_name: str,
) -> None:
    """Upload *frame*, submit Real-ESRGAN workflow, save output as ``frame_{frame_num:06d}.png``."""
    comfy_name = await client.upload_image(frame)
    workflow = _build_workflow(workflow_template, comfy_name, model_name)

    job_id = await client.submit(workflow)
    try:
        await client.wait(job_id)
    except ComfyJobError as exc:
        _check_oom(exc)
        raise

    fetch_dir = out_dir / f".fetch_{frame_num:06d}"
    try:
        downloaded = await client.fetch_outputs(job_id, fetch_dir)
        if not downloaded:
            raise UpscaleError(f"No output file returned for frame {frame_num}")

        src = sorted(downloaded, key=lambda p: p.name)[0]
        dest = out_dir / f"frame_{frame_num:06d}.png"
        shutil.copy2(src, dest)
        logger.debug("Saved upscaled frame: %s", dest.name)
    finally:
        if fetch_dir.exists():
            shutil.rmtree(fetch_dir)


def _choose_model(input_w: int, input_h: int, target_w: int, target_h: int) -> str:
    """Return the Real-ESRGAN model name best suited for the required upscale ratio."""
    scale = max(target_w / input_w, target_h / input_h)
    return _MODEL_4X if scale > 2.0 else _MODEL_2X


def _get_video_resolution(video: Path) -> tuple[int, int]:
    """Return ``(width, height)`` of the first video stream in *video*."""
    result = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise UpscaleError(
            f"ffprobe failed for {video}: {result.stderr.decode(errors='replace').strip()}"
        )
    raw = result.stdout.decode().strip()
    if not raw:
        raise UpscaleError(f"ffprobe returned no resolution for {video}")
    w_str, h_str = raw.split(",", 1)
    return int(w_str), int(h_str)


def _build_workflow(
    template: dict[str, Any],
    frame_filename: str,
    model_name: str,
) -> dict[str, Any]:
    """Return a workflow dict with stub values replaced.

    Substitutes:
    - ``LoadImage`` with ``image = "loom_input_image"`` → *frame_filename*
    - ``UpscaleModelLoader`` with ``model_name = "loom_model_name"`` → *model_name*

    Raises
    ------
    UpscaleError
        If the template has no ``loom_input_image`` LoadImage node.
    """
    wf = copy.deepcopy(template)

    found_input = False
    for node in wf.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if node.get("class_type") == "LoadImage" and inputs.get("image") == "loom_input_image":
            inputs["image"] = frame_filename
            found_input = True
        if (
            node.get("class_type") == "UpscaleModelLoader"
            and inputs.get("model_name") == "loom_model_name"
        ):
            inputs["model_name"] = model_name

    if not found_input:
        raise UpscaleError(
            "Workflow template missing a LoadImage node with image='loom_input_image'"
        )

    return wf


def _check_oom(exc: ComfyJobError) -> None:
    """Raise :class:`OOMError` if *exc* looks like a GPU out-of-memory error."""
    msg_lower = str(exc).lower()
    if any(marker in msg_lower for marker in _OOM_MARKERS):
        raise OOMError(
            f"ComfyUI ran out of GPU memory (job {exc.job_id}). "
            "To recover: enable ComfyUI smart memory offloading "
            "(--lowvram / --novram launch flags)."
        ) from exc


def _encode_frames_scaled(
    frame_dir: Path,
    output: Path,
    fps: float,
    target_w: int,
    target_h: int,
) -> None:
    """Encode PNG frames in *frame_dir* to *output* at *fps*, scaling to *target_w*×*target_h*."""
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frame_dir / "frame_%06d.png"),
            "-vf",
            f"scale={target_w}:{target_h}:flags=lanczos",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise UpscaleError(
            f"ffmpeg encode failed: {result.stderr.decode(errors='replace').strip()}"
        )
    logger.info("Encoded %.3f-fps video scaled to %dx%d: %s", fps, target_w, target_h, output)
