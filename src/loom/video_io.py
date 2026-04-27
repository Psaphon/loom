"""ffmpeg helpers for frame extraction and video encoding."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoIOError(Exception):
    """Raised when an ffmpeg operation fails."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess command, raising VideoIOError on failure."""
    logger.debug("Running: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True)  # noqa: S603
    if result.returncode != 0:
        raise VideoIOError(
            f"Command failed (exit {result.returncode}): {' '.join(str(c) for c in cmd)}\n"
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result


def get_video_fps(video: Path) -> float:
    """Return the frame rate of *video* using ffprobe.

    Raises
    ------
    VideoIOError
        If ffprobe fails or the stream has no readable frame rate.
    """
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "csv=p=0",
            str(video),
        ]
    )
    raw = result.stdout.decode().strip()
    if not raw:
        raise VideoIOError(f"ffprobe returned no frame rate for {video}")
    if "/" in raw:
        num, den = raw.split("/", 1)
        return float(num) / float(den)
    return float(raw)


def extract_frames(video: Path, out_dir: Path) -> tuple[list[Path], float]:
    """Extract every frame from *video* as PNG files into *out_dir*.

    Parameters
    ----------
    video:
        Source video file.
    out_dir:
        Directory to write ``frame_NNNNNN.png`` files into.  Created if absent.

    Returns
    -------
    tuple[list[Path], float]
        Sorted list of extracted frame paths and the source FPS.

    Raises
    ------
    VideoIOError
        If ffprobe or ffmpeg fails.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = get_video_fps(video)

    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            str(out_dir / "frame_%06d.png"),
        ]
    )

    frames = sorted(out_dir.glob("frame_*.png"))
    logger.info("Extracted %d frames (%.3f fps) from %s", len(frames), fps, video)
    return frames, fps


def encode_frames(frame_dir: Path, output: Path, fps: float) -> None:
    """Encode PNG frames in *frame_dir* into *output* video at *fps*.

    Frames must be named ``frame_NNNNNN.png`` (six-digit zero-padded, 1-based).

    Parameters
    ----------
    frame_dir:
        Directory containing ``frame_NNNNNN.png`` files.
    output:
        Destination video file path.
    fps:
        Frame rate for the output video.

    Raises
    ------
    VideoIOError
        If ffmpeg fails.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frame_dir / "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )
    logger.info("Encoded %d-fps video: %s", fps, output)
