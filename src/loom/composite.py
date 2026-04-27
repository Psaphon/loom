"""ffmpeg filter-graph builder and runner for overlay compositing."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from loom.config import LoomConfig
from loom.schemas import SongStructure
from loom.timeline import Segment, build_segments
from loom.video_io import VideoIOError, get_video_duration

logger = logging.getLogger(__name__)


class CompositeError(Exception):
    """Raised when the compositing step fails."""


# ffmpeg blend filter all_expr templates.
# A = overlay pixel (top layer), B = base pixel (bottom layer); values in [0, 255].
# Each template blends A over B then mixes by opacity:
#   result = blend_result * {op} + B * {op_inv}
# Single-quotes in the filter_complex string protect commas from being
# interpreted as filter-chain separators by ffmpeg's parser.
_BLEND_EXPRS: dict[str, str] = {
    "normal": "A*{op}+B*{op_inv}",
    "add": "if(gt(A+B,255),255,A+B)*{op}+B*{op_inv}",
    "multiply": "(A*B/255)*{op}+B*{op_inv}",
    "screen": "(255-(255-A)*(255-B)/255)*{op}+B*{op_inv}",
    "overlay": "if(lt(B,128),2*A*B/255,255-2*(255-A)*(255-B)/255)*{op}+B*{op_inv}",
    "lighten": "if(gt(A,B),A,B)*{op}+B*{op_inv}",
    "darken": "if(lt(A,B),A,B)*{op}+B*{op_inv}",
    "subtract": "if(gt(B-A,0),B-A,0)*{op}+B*{op_inv}",
    "hardlight": "if(lt(A,128),2*A*B/255,255-2*(255-A)*(255-B)/255)*{op}+B*{op_inv}",
    "softlight": "((1-2*A/255)*B*B/255+2*(A/255)*B)*{op}+B*{op_inv}",
}


def _blend_expr(blend: str, opacity: float) -> str:
    """Return the ffmpeg blend all_expr for *blend* mode at *opacity*."""
    template = _BLEND_EXPRS.get(blend, _BLEND_EXPRS["normal"])
    op_inv = round(1.0 - opacity, 6)
    return template.format(op=opacity, op_inv=op_inv)


def _duration_adj_filter(idx: int, base_duration: float, ovl_duration: float, loop: bool) -> str:
    """Return a filter-chain line that adjusts overlay *idx* to *base_duration*.

    Input label:  ``[ovl{idx}_scaled]``
    Output label: ``[ovl{idx}]``
    """
    i = idx
    parts: list[str] = []

    if ovl_duration < base_duration:
        if loop:
            # Repeat the whole overlay video indefinitely, then trim.
            # size=99999 safely covers any overlay up to ~69 min at 24 fps.
            parts.append("loop=loop=-1:size=99999:start=0")
        else:
            # Hold the last frame for the remaining duration.
            extra = base_duration - ovl_duration
            parts.append(f"tpad=stop_mode=clone:stop_duration={extra:.6f}")

    parts.append(f"trim=duration={base_duration:.6f}")
    parts.append("setpts=PTS-STARTPTS")

    chain = ",".join(parts)
    return f"[ovl{i}_scaled]{chain}[ovl{i}]"


def _overlay_blend_filters(
    idx: int,
    segments: list[Segment],
    base_label: str,
) -> tuple[list[str], str]:
    """Build filter lines to blend overlay *idx* onto *base_label*.

    Returns ``(filter_lines, result_label)``.
    For a single segment the result is a simple ``blend`` call.
    For multiple segments the stream is split, each segment is trimmed and
    blended independently, then they are concatenated back together.
    """
    i = idx
    ovl = f"ovl{i}"
    result = f"result{i}"
    n = len(segments)
    lines: list[str] = []

    if n == 1:
        expr = _blend_expr(segments[0].blend, segments[0].opacity)
        lines.append(f"[{ovl}][{base_label}]blend=all_expr='{expr}'[{result}]")
        return lines, result

    # Split base and overlay each into n copies for per-segment processing.
    b_labels = [f"b{i}_{j}" for j in range(n)]
    o_labels = [f"o{i}_{j}" for j in range(n)]
    b_out = "".join(f"[{lbl}]" for lbl in b_labels)
    o_out = "".join(f"[{lbl}]" for lbl in o_labels)
    lines.append(f"[{base_label}]split={n}{b_out}")
    lines.append(f"[{ovl}]split={n}{o_out}")

    seg_labels: list[str] = []
    for j, seg in enumerate(segments):
        ts = f"start={seg.start:.6f}:end={seg.end:.6f}"
        lines.append(f"[{b_labels[j]}]trim={ts},setpts=PTS-STARTPTS[bs{i}_{j}]")
        lines.append(f"[{o_labels[j]}]trim={ts},setpts=PTS-STARTPTS[os{i}_{j}]")

        expr = _blend_expr(seg.blend, seg.opacity)
        seg_lbl = f"seg{i}_{j}"
        lines.append(f"[os{i}_{j}][bs{i}_{j}]blend=all_expr='{expr}'[{seg_lbl}]")
        seg_labels.append(seg_lbl)

    segs_in = "".join(f"[{lbl}]" for lbl in seg_labels)
    lines.append(f"{segs_in}concat=n={n}:v=1:a=0[{result}]")
    return lines, result


def _build_no_overlay_cmd(
    base: Path,
    output: Path,
    width: int,
    height: int,
    fps: int,
) -> list[str]:
    """Build ffmpeg command when there are no overlays — just scale/fps the base."""
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(base),
        "-vf",
        f"fps={fps},scale={width}:{height}:flags=lanczos",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]


def _build_composite_cmd(
    base: Path,
    overlays: list[Path],
    output: Path,
    segments: list[Segment],
    base_duration: float,
    ovl_durations: list[float],
    width: int,
    height: int,
    fps: int,
    loop: bool,
) -> list[str]:
    """Build the full ffmpeg command for compositing M overlays onto base."""
    # Inputs
    inputs: list[str] = ["-y", "-i", str(base)]
    for ovl in overlays:
        inputs.extend(["-i", str(ovl)])

    fc_parts: list[str] = []

    # Scale base and set fps
    fc_parts.append(f"[0:v]fps={fps},scale={width}:{height}:flags=lanczos[base_scaled]")

    # Scale each overlay and adjust its duration
    for i, (ovl_path, ovl_dur) in enumerate(zip(overlays, ovl_durations, strict=True)):
        del ovl_path  # used only for input ordering; path already in inputs
        fc_parts.append(f"[{i + 1}:v]scale={width}:{height}:flags=lanczos[ovl{i}_scaled]")
        fc_parts.append(_duration_adj_filter(i, base_duration, ovl_dur, loop))

    # Chain overlays: each composite result becomes the base for the next
    current_base = "base_scaled"
    for i in range(len(overlays)):
        blend_lines, result_label = _overlay_blend_filters(i, segments, current_base)
        fc_parts.extend(blend_lines)
        current_base = result_label

    fc = ";".join(fc_parts)

    return [
        "ffmpeg",
        *inputs,
        "-filter_complex",
        fc,
        "-map",
        f"[{current_base}]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]


def run_composite(cfg: LoomConfig) -> Path:
    """Composite overlays onto base footage according to *cfg*.

    Reads song structure from ``{song_stem}.structure.json`` beside the song
    file to build per-section opacity/blend segments.  Writes the result to
    ``{cfg.paths.output}/composite.mp4`` (no audio).

    Raises
    ------
    CompositeError
        If required files are missing or ffmpeg fails.
    """
    if not cfg.paths.base.exists():
        raise CompositeError(f"Base video not found: {cfg.paths.base}")

    structure_json = cfg.paths.song.parent / f"{cfg.paths.song.stem}.structure.json"
    if not structure_json.exists():
        raise CompositeError(
            f"Song structure JSON not found: {structure_json}. Run 'loom analyze' first."
        )

    for ovl in cfg.paths.overlays:
        if not ovl.exists():
            raise CompositeError(f"Overlay video not found: {ovl}")

    # Load song structure and build timeline segments
    structure = SongStructure.from_json(structure_json)

    try:
        base_duration = get_video_duration(cfg.paths.base)
    except VideoIOError as exc:
        raise CompositeError(f"Cannot read base video duration: {exc}") from exc

    segments = build_segments(structure, cfg.timeline, base_duration)

    # Prepare output directory
    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "composite.mp4"

    logger.info(
        "composite: base=%s overlays=%d segments=%d → %s",
        cfg.paths.base,
        len(cfg.paths.overlays),
        len(segments),
        output,
    )

    if not cfg.paths.overlays:
        cmd = _build_no_overlay_cmd(
            cfg.paths.base, output, cfg.render.width, cfg.render.height, cfg.render.fps
        )
    else:
        ovl_durations: list[float] = []
        for ovl in cfg.paths.overlays:
            try:
                ovl_durations.append(get_video_duration(ovl))
            except VideoIOError as exc:
                raise CompositeError(f"Cannot read overlay duration for {ovl}: {exc}") from exc

        cmd = _build_composite_cmd(
            cfg.paths.base,
            cfg.paths.overlays,
            output,
            segments,
            base_duration,
            ovl_durations,
            cfg.render.width,
            cfg.render.height,
            cfg.render.fps,
            cfg.render.overlay_loop,
        )

    logger.debug("ffmpeg: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True)  # noqa: S603
    if result.returncode != 0:
        raise CompositeError(
            f"ffmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr.decode(errors='replace').strip()}"
        )

    logger.info("composite: written %s", output)
    return output
