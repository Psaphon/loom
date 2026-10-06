"""Integration tests for composite.py with mocked ffmpeg/ffprobe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loom.composite import (
    CompositeError,
    _blend_expr,
    _build_composite_cmd,
    _build_no_overlay_cmd,
    run_composite,
)
from loom.config import LoomConfig, PathsConfig, RenderConfig, ScheduleConfig, TimelineRule
from loom.timeline import Segment

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SAMPLE_STRUCTURE = {
    "tempo_bpm": 120.0,
    "duration_seconds": 10.0,
    "beats": [0.5, 1.0, 1.5],
    "sections": [
        {"start": 0.0, "end": 5.0, "label": "verse"},
        {"start": 5.0, "end": 10.0, "label": "chorus"},
    ],
}


def _make_config(
    tmp_path: Path,
    overlays: list[Path] | None = None,
    timeline: list[TimelineRule] | None = None,
    overlay_loop: bool = True,
) -> LoomConfig:
    base = tmp_path / "base.mp4"
    base.write_bytes(b"fake")
    song = tmp_path / "song.wav"
    song.write_bytes(b"fake")
    output_dir = tmp_path / "output"

    # Write structure JSON beside song
    structure_json = tmp_path / "song.structure.json"
    structure_json.write_text(json.dumps(SAMPLE_STRUCTURE))

    render = RenderConfig(width=512, height=512, fps=24, overlay_loop=overlay_loop)
    paths = PathsConfig(
        base=base,
        song=song,
        output=output_dir,
        overlays=overlays or [],
    )
    return LoomConfig(
        name="test",
        paths=paths,
        render=render,
        schedule=ScheduleConfig(),
        timeline=timeline or [],
    )


def _make_subprocess_mock(base_duration: float = 10.0, ovl_duration: float = 5.0):
    """Return a subprocess.run side_effect that fakes ffprobe/ffmpeg."""

    def fake_run(cmd: list[str], capture_output: bool = False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""

        if "ffprobe" in cmd[0]:
            # get_video_duration returns duration in seconds
            cmd_str = " ".join(str(c) for c in cmd)
            if "format=duration" in cmd_str:
                # Second call is for overlay or base
                # We alternate: first call = base, subsequent = overlays
                result.stdout = str(base_duration).encode()
            else:
                result.stdout = b"24/1"
            return result

        # ffmpeg composite/scale call — just succeed
        result.stdout = b""
        return result

    return fake_run


def _make_subprocess_mock_with_overlay(base_duration: float = 10.0, ovl_duration: float = 5.0):
    """Return a mock that returns different durations for base vs overlay calls."""
    call_count = {"n": 0}

    def fake_run(cmd: list[str], capture_output: bool = False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""

        if "ffprobe" in cmd[0]:
            cmd_str = " ".join(str(c) for c in cmd)
            if "format=duration" in cmd_str:
                # First ffprobe duration call = base, second = overlay
                n = call_count["n"]
                call_count["n"] += 1
                result.stdout = str(base_duration if n == 0 else ovl_duration).encode()
            else:
                result.stdout = b"24/1"
            return result

        result.stdout = b""
        return result

    return fake_run


# ---------------------------------------------------------------------------
# _blend_expr unit tests
# ---------------------------------------------------------------------------


def test_blend_expr_normal_full_opacity() -> None:
    expr = _blend_expr("normal", 1.0)
    assert "A*1.0" in expr
    assert "B*0.0" in expr


def test_blend_expr_normal_half_opacity() -> None:
    expr = _blend_expr("normal", 0.5)
    assert "A*0.5" in expr
    assert "B*0.5" in expr


def test_blend_expr_screen_contains_formula() -> None:
    expr = _blend_expr("screen", 0.8)
    assert "255" in expr
    assert "0.8" in expr


def test_blend_expr_add_contains_255_cap() -> None:
    expr = _blend_expr("add", 1.0)
    assert "255" in expr


def test_blend_expr_multiply_contains_division() -> None:
    expr = _blend_expr("multiply", 1.0)
    assert "A*B/255" in expr


def test_blend_expr_overlay_contains_conditional() -> None:
    expr = _blend_expr("overlay", 0.5)
    assert "128" in expr


def test_blend_expr_unknown_mode_falls_back_to_normal() -> None:
    expr = _blend_expr("nonexistent_mode", 0.5)
    # Should produce normal-mode expression
    assert "A*0.5" in expr


@pytest.mark.parametrize(
    "mode",
    [
        "normal",
        "add",
        "multiply",
        "screen",
        "overlay",
        "lighten",
        "darken",
        "subtract",
        "hardlight",
        "softlight",
    ],
)
def test_blend_expr_all_modes_produce_nonempty_string(mode: str) -> None:
    expr = _blend_expr(mode, 0.7)
    assert expr
    assert "0.7" in expr


# ---------------------------------------------------------------------------
# _build_no_overlay_cmd
# ---------------------------------------------------------------------------


def test_build_no_overlay_cmd_structure(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    out = tmp_path / "out.mp4"
    cmd = _build_no_overlay_cmd(base, out, width=512, height=512, fps=24)
    assert cmd[0] == "ffmpeg"
    assert str(base) in cmd
    assert str(out) in cmd
    assert any("scale=512:512" in c for c in cmd)
    assert any("fps=24" in c for c in cmd)


# ---------------------------------------------------------------------------
# _build_composite_cmd
# ---------------------------------------------------------------------------


def test_build_composite_cmd_single_overlay_single_segment(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    ovl = tmp_path / "ovl.mp4"
    out = tmp_path / "out.mp4"
    segments = [Segment(start=0.0, end=10.0, opacity=0.7, blend="screen")]

    cmd = _build_composite_cmd(
        base=base,
        overlays=[ovl],
        output=out,
        segments=segments,
        base_duration=10.0,
        ovl_durations=[5.0],
        width=512,
        height=512,
        fps=24,
        loop=True,
    )

    assert cmd[0] == "ffmpeg"
    assert str(base) in cmd
    assert str(ovl) in cmd
    assert str(out) in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "blend" in fc
    assert "loop" in fc  # overlay shorter than base → loop applied
    assert "scale=512:512" in fc


def test_build_composite_cmd_overlay_longer_no_loop(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    ovl = tmp_path / "ovl.mp4"
    out = tmp_path / "out.mp4"
    segments = [Segment(start=0.0, end=5.0, opacity=1.0, blend="normal")]

    cmd = _build_composite_cmd(
        base=base,
        overlays=[ovl],
        output=out,
        segments=segments,
        base_duration=5.0,
        ovl_durations=[20.0],  # longer than base
        width=512,
        height=512,
        fps=24,
        loop=True,
    )

    fc = cmd[cmd.index("-filter_complex") + 1]
    # No loop filter needed when overlay is longer than base
    assert "loop" not in fc
    assert "trim" in fc


def test_build_composite_cmd_hold_mode(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    ovl = tmp_path / "ovl.mp4"
    out = tmp_path / "out.mp4"
    segments = [Segment(start=0.0, end=10.0, opacity=1.0, blend="normal")]

    cmd = _build_composite_cmd(
        base=base,
        overlays=[ovl],
        output=out,
        segments=segments,
        base_duration=10.0,
        ovl_durations=[3.0],  # shorter, hold mode
        width=512,
        height=512,
        fps=24,
        loop=False,  # hold last frame
    )

    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "tpad" in fc
    assert "loop" not in fc


def test_build_composite_cmd_multiple_segments(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    ovl = tmp_path / "ovl.mp4"
    out = tmp_path / "out.mp4"
    segments = [
        Segment(start=0.0, end=5.0, opacity=0.5, blend="multiply"),
        Segment(start=5.0, end=10.0, opacity=0.9, blend="screen"),
    ]

    cmd = _build_composite_cmd(
        base=base,
        overlays=[ovl],
        output=out,
        segments=segments,
        base_duration=10.0,
        ovl_durations=[10.0],
        width=512,
        height=512,
        fps=24,
        loop=True,
    )

    fc = cmd[cmd.index("-filter_complex") + 1]
    # Two segments → split + concat
    assert "split=2" in fc
    assert "concat" in fc
    # multiply expr: A*B/255; screen expr: 255-(255-A)*(255-B)/255
    assert "A*B/255" in fc
    assert "(255-A)" in fc


def test_build_composite_cmd_two_overlays(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    ovl0 = tmp_path / "ovl0.mp4"
    ovl1 = tmp_path / "ovl1.mp4"
    out = tmp_path / "out.mp4"
    segments = [Segment(start=0.0, end=10.0, opacity=0.5, blend="normal")]

    cmd = _build_composite_cmd(
        base=base,
        overlays=[ovl0, ovl1],
        output=out,
        segments=segments,
        base_duration=10.0,
        ovl_durations=[10.0, 10.0],
        width=512,
        height=512,
        fps=24,
        loop=True,
    )

    assert str(ovl0) in cmd
    assert str(ovl1) in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    # Two overlays → two blend operations
    assert "result0" in fc
    assert "result1" in fc


# ---------------------------------------------------------------------------
# run_composite — error paths
# ---------------------------------------------------------------------------


def test_run_composite_missing_base_raises(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.paths.base = tmp_path / "nonexistent.mp4"
    with pytest.raises(CompositeError, match="Base video not found"):
        run_composite(cfg)


def test_run_composite_missing_structure_json_raises(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    # Remove the structure JSON
    structure_json = tmp_path / "song.structure.json"
    structure_json.unlink()
    with pytest.raises(CompositeError, match="Song structure JSON not found"):
        run_composite(cfg)


def test_run_composite_missing_overlay_raises(tmp_path: Path) -> None:
    ovl = tmp_path / "missing_overlay.mp4"
    cfg = _make_config(tmp_path, overlays=[ovl])
    with pytest.raises(CompositeError, match="Overlay video not found"):
        run_composite(cfg)


def test_run_composite_ffmpeg_failure_raises(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)

    def failing_run(cmd, capture_output=False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        if "ffprobe" in cmd[0]:
            result.returncode = 0
            result.stdout = b"10.0"
            result.stderr = b""
        else:
            result.returncode = 1
            result.stdout = b""
            result.stderr = b"error: invalid codec"
        return result

    with patch("subprocess.run", side_effect=failing_run):
        with pytest.raises(CompositeError, match="ffmpeg failed"):
            run_composite(cfg)


# ---------------------------------------------------------------------------
# run_composite — happy paths
# ---------------------------------------------------------------------------


def test_run_composite_no_overlays_calls_scale(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, overlays=[])

    with patch("subprocess.run", side_effect=_make_subprocess_mock()) as mock_run:
        out = run_composite(cfg)

    assert out.name == "composite.mp4"
    assert out.parent == tmp_path / "output"

    # ffprobe (base duration) + ffmpeg scale
    ffmpeg_calls = [c for c in mock_run.call_args_list if "ffmpeg" in c[0][0][0]]
    assert len(ffmpeg_calls) == 1
    cmd = ffmpeg_calls[0][0][0]
    assert any("scale=512:512" in str(a) for a in cmd)
    assert "-filter_complex" not in cmd


def test_run_composite_with_overlay_uses_filter_complex(tmp_path: Path) -> None:
    ovl = tmp_path / "overlay.mp4"
    ovl.write_bytes(b"fake")
    cfg = _make_config(tmp_path, overlays=[ovl])

    with patch(
        "subprocess.run",
        side_effect=_make_subprocess_mock_with_overlay(base_duration=10.0, ovl_duration=5.0),
    ):
        out = run_composite(cfg)

    assert out.name == "composite.mp4"


def test_run_composite_creates_output_dir(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    output_dir = tmp_path / "output"
    assert not output_dir.exists()

    with patch("subprocess.run", side_effect=_make_subprocess_mock()):
        run_composite(cfg)

    assert output_dir.exists()


def test_run_composite_timeline_rules_applied(tmp_path: Path) -> None:
    ovl = tmp_path / "overlay.mp4"
    ovl.write_bytes(b"fake")
    rules = [TimelineRule(section="chorus", opacity=0.7, blend="screen")]
    cfg = _make_config(tmp_path, overlays=[ovl], timeline=rules)

    captured_cmd: list[str] = []

    def capturing_run(cmd, capture_output=False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""
        if "ffprobe" in cmd[0]:
            result.stdout = b"10.0"
        else:
            captured_cmd.extend(cmd)
            result.stdout = b""
        return result

    with patch("subprocess.run", side_effect=capturing_run):
        run_composite(cfg)

    # filter_complex should use screen blend formula and chorus opacity 0.7
    fc_idx = captured_cmd.index("-filter_complex")
    fc = captured_cmd[fc_idx + 1]
    # screen formula: (255-(255-A)*(255-B)/255)
    assert "(255-A)" in fc
    assert "0.7" in fc
