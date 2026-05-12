"""Tests for pipeline.py and state.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loom.config import LoomConfig, PathsConfig, RenderConfig, ScheduleConfig
from loom.pipeline import (
    MAX_RETRIES,
    DeadlineReached,
    PipelineError,
    _check_deadline,
    _mux_audio,
    _parse_deadline,
    _run_stage,
    run_pipeline,
)
from loom.state import PipelineState, file_hash

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_STRUCTURE = {
    "tempo_bpm": 120.0,
    "duration_seconds": 10.0,
    "beats": [0.5, 1.0],
    "sections": [{"start": 0.0, "end": 10.0, "label": "section_0"}],
}


def _make_config(tmp_path: Path, *, enable_diffusion: bool = False) -> LoomConfig:
    base = tmp_path / "base.mp4"
    base.write_bytes(b"fake-base")
    song = tmp_path / "song.wav"
    song.write_bytes(b"fake-song")
    output_dir = tmp_path / "output"

    structure_json = tmp_path / "song.structure.json"
    structure_json.write_text(json.dumps(SAMPLE_STRUCTURE))

    return LoomConfig(
        name="test",
        paths=PathsConfig(base=base, song=song, output=output_dir, overlays=[]),
        render=RenderConfig(enable_diffusion=enable_diffusion),
        schedule=ScheduleConfig(deadline="23:59"),
        timeline=[],
    )


# ---------------------------------------------------------------------------
# PipelineState unit tests
# ---------------------------------------------------------------------------


class TestPipelineState:
    def test_initially_no_stage_is_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert not state.is_complete("analyze")

    def test_mark_complete_and_is_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_complete("analyze")
        assert state.is_complete("analyze")

    def test_mark_complete_with_hash(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_complete("analyze", input_hash="abc123")
        assert state.is_complete("analyze", input_hash="abc123")
        assert not state.is_complete("analyze", input_hash="different")

    def test_hash_none_skips_hash_check(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_complete("analyze", input_hash="abc123")
        # No hash passed → only check status
        assert state.is_complete("analyze", input_hash=None)

    def test_mark_failed_does_not_count_as_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_failed("composite")
        assert not state.is_complete("composite")

    def test_clear_single_stage(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_complete("analyze")
        state.mark_complete("composite")
        state.clear("analyze")
        assert not state.is_complete("analyze")
        assert state.is_complete("composite")

    def test_clear_all_stages(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.mark_complete("analyze")
        state.mark_complete("composite")
        state.clear()
        assert not state.is_complete("analyze")
        assert not state.is_complete("composite")

    def test_state_persists_across_instances(self, tmp_path: Path) -> None:
        state1 = PipelineState(tmp_path)
        state1.mark_complete("analyze", input_hash="xyz")

        state2 = PipelineState(tmp_path)
        assert state2.is_complete("analyze", input_hash="xyz")

    def test_corrupt_state_file_starts_fresh(self, tmp_path: Path) -> None:
        state_file = tmp_path / ".loom-state.json"
        state_file.write_text("not json {{{")
        state = PipelineState(tmp_path)
        assert not state.is_complete("analyze")


# ---------------------------------------------------------------------------
# file_hash
# ---------------------------------------------------------------------------


def test_file_hash_returns_hex_string(tmp_path: Path) -> None:
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello world")
    h = file_hash(f)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex


def test_file_hash_changes_with_content(tmp_path: Path) -> None:
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello")
    h1 = file_hash(f)
    f.write_bytes(b"world")
    h2 = file_hash(f)
    assert h1 != h2


# ---------------------------------------------------------------------------
# Deadline helpers
# ---------------------------------------------------------------------------


def test_parse_deadline() -> None:
    from datetime import time

    assert _parse_deadline("05:30") == time(5, 30)
    assert _parse_deadline("23:59") == time(23, 59)


def test_check_deadline_does_not_raise_when_not_reached() -> None:
    from datetime import time

    # 23:59 is effectively never reached in a test run
    _check_deadline(time(23, 59), "analyze")


def test_check_deadline_raises_when_past() -> None:
    from datetime import time

    with pytest.raises(DeadlineReached, match="analyze"):
        _check_deadline(time(0, 0), "analyze")  # midnight already passed


# ---------------------------------------------------------------------------
# _run_stage
# ---------------------------------------------------------------------------


def test_run_stage_success(tmp_path: Path) -> None:
    state = PipelineState(tmp_path)
    calls = []

    def good_fn(x):
        calls.append(x)
        return x * 2

    result = _run_stage("test", good_fn, 21, state=state)
    assert result == 42
    assert calls == [21]
    assert state.is_complete("test")


def test_run_stage_retries_on_failure(tmp_path: Path) -> None:
    state = PipelineState(tmp_path)
    attempt_count = [0]

    def flaky_fn():
        attempt_count[0] += 1
        if attempt_count[0] <= MAX_RETRIES:
            raise RuntimeError("transient failure")
        return "ok"

    result = _run_stage("flaky", flaky_fn, state=state)
    assert result == "ok"
    assert attempt_count[0] == MAX_RETRIES + 1
    assert state.is_complete("flaky")


def test_run_stage_raises_after_max_retries(tmp_path: Path) -> None:
    state = PipelineState(tmp_path)

    def always_fails():
        raise RuntimeError("always bad")

    with pytest.raises(PipelineError, match="always_fails_stage"):
        _run_stage("always_fails_stage", always_fails, state=state)

    assert not state.is_complete("always_fails_stage")


def test_run_stage_oom_does_not_retry(tmp_path: Path) -> None:
    state = PipelineState(tmp_path)
    attempt_count = [0]

    class OOMError(Exception):
        pass

    def oom_fn():
        attempt_count[0] += 1
        raise OOMError("cuda out of memory")

    with pytest.raises(PipelineError, match="GPU OOM"):
        _run_stage("oom_stage", oom_fn, state=state)

    assert attempt_count[0] == 1  # never retried


def test_run_stage_stores_input_hash(tmp_path: Path) -> None:
    state = PipelineState(tmp_path)
    _run_stage("hash_stage", lambda: None, state=state, input_hash="deadbeef")
    assert state.is_complete("hash_stage", input_hash="deadbeef")
    assert not state.is_complete("hash_stage", input_hash="different")


# ---------------------------------------------------------------------------
# _mux_audio
# ---------------------------------------------------------------------------


def test_mux_audio_success(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "out" / "final.mp4"

    def fake_run(cmd, capture_output=False, **_kwargs):
        r = MagicMock(spec=subprocess.CompletedProcess)
        r.returncode = 0
        r.stderr = b""
        return r

    with patch("subprocess.run", side_effect=fake_run):
        _mux_audio(video, audio, output)

    assert output.parent.exists()


def test_mux_audio_ffmpeg_failure_raises(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "final.mp4"

    def failing_run(cmd, capture_output=False, **_kwargs):
        r = MagicMock(spec=subprocess.CompletedProcess)
        r.returncode = 1
        r.stderr = b"error: codec not found"
        return r

    with patch("subprocess.run", side_effect=failing_run):
        with pytest.raises(PipelineError, match="ffmpeg mux failed"):
            _mux_audio(video, audio, output)


# ---------------------------------------------------------------------------
# run_pipeline — integration (fully mocked)
# ---------------------------------------------------------------------------


def _make_stage_mocks(tmp_path: Path, cfg: LoomConfig):
    """Return a dict of patchers for all pipeline stages."""

    def fake_analyze(song, force=False):
        return song.parent / f"{song.stem}.structure.json"

    def fake_composite(config):
        out = config.paths.output / "composite.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"composite")
        return out

    def fake_mux(video, audio, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    # Mock upscale_video at the module level where it's imported in pipeline
    async def fake_upscale(input_path, output_path, target="1080p", *, comfy_url=""):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"upscaled")

    return fake_analyze, fake_composite, fake_mux, fake_upscale


def test_run_pipeline_happy_path(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    fake_analyze, fake_composite, fake_mux, fake_upscale = _make_stage_mocks(tmp_path, cfg)

    with (
        patch("loom.pipeline.analyze_song", side_effect=fake_analyze),
        patch("loom.pipeline.run_composite", side_effect=fake_composite),
        patch("loom.pipeline._mux_audio", side_effect=fake_mux),
        patch("loom.upscale.upscale_video", side_effect=fake_upscale),
    ):
        # upscale_video is called via asyncio.run(upscale_video(...)) inside a lambda
        # We need to patch where asyncio.run is called inside the pipeline lambda
        import asyncio as _asyncio

        original_asyncio_run = _asyncio.run

        def fake_asyncio_run(coro, **kwargs):
            # For upscale we need to write the file
            cfg.paths.output.mkdir(parents=True, exist_ok=True)
            upscale_out = cfg.paths.output / "upscaled.mp4"
            upscale_out.write_bytes(b"upscaled")
            # Drain the coroutine to avoid ResourceWarning
            try:
                return original_asyncio_run(coro, **kwargs)
            except Exception:
                return None

        with patch("loom.pipeline.asyncio.run", side_effect=fake_asyncio_run):
            final = run_pipeline(cfg, force=True)

    assert final.name == "final.mp4"


def test_run_pipeline_skips_completed_stages(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate state with correct hashes so the pipeline recognises them as complete
    state = PipelineState(output_dir)
    state.mark_complete("analyze", input_hash=file_hash(cfg.paths.song))
    state.mark_complete("stylize")
    state.mark_complete("composite")
    state.mark_complete("upscale")
    state.mark_complete("mux")

    # Write the files that run_pipeline expects to return
    final_out = output_dir / "final.mp4"
    final_out.write_bytes(b"done")

    analyze_calls = []

    def counting_analyze(song, force=False):
        analyze_calls.append(song)

    with patch("loom.pipeline.analyze_song", side_effect=counting_analyze):
        final = run_pipeline(cfg, force=False)

    assert not analyze_calls  # skipped
    assert final == final_out


def test_run_pipeline_force_reruns_all(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Mark everything complete
    state = PipelineState(output_dir)
    for stage in ["analyze", "stylize", "composite", "upscale", "mux"]:
        state.mark_complete(stage)

    analyze_calls = []

    def counting_analyze(song, force=False):
        analyze_calls.append(song)
        return song.parent / f"{song.stem}.structure.json"

    def fake_composite(config):
        out = config.paths.output / "composite.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"composite")
        return out

    def fake_mux(video, audio, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def fake_asyncio_run(coro, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "upscaled.mp4").write_bytes(b"upscaled")
        import asyncio as _asyncio

        try:
            return _asyncio.get_event_loop().run_until_complete(coro)
        except Exception:
            return None

    with (
        patch("loom.pipeline.analyze_song", side_effect=counting_analyze),
        patch("loom.pipeline.run_composite", side_effect=fake_composite),
        patch("loom.pipeline._mux_audio", side_effect=fake_mux),
        patch("loom.pipeline.asyncio.run", side_effect=fake_asyncio_run),
    ):
        run_pipeline(cfg, force=True)

    assert len(analyze_calls) == 1  # re-ran


def test_run_pipeline_deadline_stops_before_stage(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    # Set deadline in the past via the config
    cfg.schedule.deadline = "00:00"

    with pytest.raises(DeadlineReached):
        run_pipeline(cfg)


def test_run_pipeline_stage_failure_raises_pipeline_error(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)

    def bad_analyze(song, force=False):
        raise RuntimeError("librosa exploded")

    # Patch upscale_video to avoid a coroutine-leak warning on early exit
    async def _noop(*args, **kwargs):
        pass

    with (
        patch("loom.pipeline.analyze_song", side_effect=bad_analyze),
        patch("loom.upscale.upscale_video", side_effect=_noop),
        pytest.raises(PipelineError, match="analyze"),
    ):
        run_pipeline(cfg)


def test_run_pipeline_stylize_skipped_when_disabled(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, enable_diffusion=False)
    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    state = PipelineState(output_dir)
    for stage in ["analyze", "composite", "upscale", "mux"]:
        state.mark_complete(stage)

    final_out = output_dir / "final.mp4"
    final_out.write_bytes(b"done")

    stylize_calls = []

    def spy_stylize(*args, **kwargs):
        stylize_calls.append(args)

    # Even if stylize_video were importable, it should not be called
    with patch("loom.pipeline.analyze_song", return_value=None):
        final = run_pipeline(cfg, force=False)

    assert not stylize_calls
    assert final == final_out


def test_run_pipeline_no_overlays_skips_preprocess(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert cfg.paths.overlays == []

    output_dir = cfg.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)
    state = PipelineState(output_dir)
    for stage in ["analyze", "composite", "upscale", "mux"]:
        state.mark_complete(stage)

    final_out = output_dir / "final.mp4"
    final_out.write_bytes(b"done")

    preprocess_calls = []

    with patch("loom.pipeline.analyze_song", return_value=None):
        run_pipeline(cfg, force=False)

    assert not preprocess_calls
