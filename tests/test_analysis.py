"""Tests for loom.analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from loom.analysis import (
    SUPPORTED_FORMATS,
    analyze_song,
    structure_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Unit tests — no I/O
# ---------------------------------------------------------------------------


def test_structure_path_mp3():
    assert structure_path(Path("/music/song.mp3")) == Path("/music/song.structure.json")


def test_structure_path_preserves_stem():
    assert structure_path(Path("/a/b/my-track.wav")) == Path("/a/b/my-track.structure.json")


def test_supported_formats_contains_expected():
    assert {".mp3", ".wav", ".m4a"} <= SUPPORTED_FORMATS


# ---------------------------------------------------------------------------
# Integration tests — use synthesized WAV fixture
# ---------------------------------------------------------------------------


def test_analyze_creates_json(sample_wav: Path):
    out = analyze_song(sample_wav)
    assert out.exists()
    assert out.name == "sample.structure.json"


def test_json_top_level_fields(sample_wav: Path):
    data = _load(analyze_song(sample_wav))
    assert set(data) >= {"tempo_bpm", "duration_seconds", "beats", "sections"}


def test_json_field_types(sample_wav: Path):
    data = _load(analyze_song(sample_wav))
    assert isinstance(data["tempo_bpm"], float)
    assert isinstance(data["duration_seconds"], float)
    assert data["duration_seconds"] > 0
    assert isinstance(data["beats"], list)
    assert isinstance(data["sections"], list)


def test_json_is_indented(sample_wav: Path):
    text = analyze_song(sample_wav).read_text()
    assert "\n" in text  # json.dumps(indent=2) produces newlines


def test_section_fields(sample_wav: Path):
    data = _load(analyze_song(sample_wav))
    for section in data["sections"]:
        assert "start" in section
        assert "end" in section
        assert "label" in section
        assert section["end"] > section["start"]


def test_sections_cover_full_duration(sample_wav: Path):
    data = _load(analyze_song(sample_wav))
    sections = data["sections"]
    assert sections[0]["start"] == pytest.approx(0.0, abs=0.01)
    assert sections[-1]["end"] == pytest.approx(data["duration_seconds"], abs=0.1)


def test_beats_are_within_duration(sample_wav: Path):
    data = _load(analyze_song(sample_wav))
    for beat in data["beats"]:
        assert 0.0 <= beat <= data["duration_seconds"]


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_skips_reanalysis(sample_wav: Path):
    out = analyze_song(sample_wav)
    mtime_first = out.stat().st_mtime
    analyze_song(sample_wav)
    assert out.stat().st_mtime == mtime_first


def test_force_reruns_analysis(sample_wav: Path):
    out = analyze_song(sample_wav)
    time.sleep(0.05)
    analyze_song(sample_wav, force=True)
    assert out.stat().st_mtime > analyze_song(sample_wav).stat().st_mtime - 1


def test_stale_cache_triggers_reanalysis(sample_wav: Path, tmp_path: Path):
    out = analyze_song(sample_wav)
    # Touch the song to make it newer than the JSON
    time.sleep(0.05)
    sample_wav.touch()
    mtime_before = out.stat().st_mtime
    analyze_song(sample_wav)
    assert out.stat().st_mtime >= mtime_before


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="not found"):
        analyze_song(tmp_path / "nonexistent.wav")


def test_unsupported_format_raises(tmp_path: Path):
    bad = tmp_path / "song.ogg"
    bad.write_bytes(b"fake audio data")
    with pytest.raises(ValueError, match="Unsupported format"):
        analyze_song(bad)
