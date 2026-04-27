"""Unit tests for timeline.py — build_segments math."""

from __future__ import annotations

import pytest

from loom.config import TimelineRule
from loom.schemas import Section, SongStructure
from loom.timeline import Segment, build_segments

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structure(*sections: tuple[float, float, str]) -> SongStructure:
    """Build a SongStructure with the given (start, end, label) sections."""
    return SongStructure(
        tempo_bpm=120.0,
        duration_seconds=max(e for _, e, _ in sections) if sections else 0.0,
        beats=[],
        sections=[Section(start=s, end=e, label=lbl) for s, e, lbl in sections],
    )


def _rule(section: str, opacity: float = 1.0, blend: str = "normal") -> TimelineRule:
    return TimelineRule(section=section, opacity=opacity, blend=blend)


def _covers(segments: list[Segment], duration: float) -> bool:
    """Return True if segments cover [0, duration] without gaps or overlaps."""
    if not segments:
        return duration <= 0
    if segments[0].start != 0.0:
        return False
    for a, b in zip(segments, segments[1:], strict=False):
        if abs(a.end - b.start) > 1e-9:
            return False
    return abs(segments[-1].end - duration) < 1e-9


# ---------------------------------------------------------------------------
# Basic coverage
# ---------------------------------------------------------------------------


def test_no_sections_returns_single_default_segment() -> None:
    struct = _structure()
    # Override: build an empty structure manually
    struct = SongStructure(tempo_bpm=120.0, duration_seconds=0.0, beats=[], sections=[])
    segs = build_segments(struct, [], 10.0)
    assert len(segs) == 1
    assert segs[0] == Segment(start=0.0, end=10.0, opacity=1.0, blend="normal")


def test_zero_duration_returns_empty() -> None:
    struct = SongStructure(tempo_bpm=120.0, duration_seconds=10.0, beats=[], sections=[])
    segs = build_segments(struct, [], 0.0)
    assert segs == []


def test_negative_duration_returns_empty() -> None:
    struct = SongStructure(tempo_bpm=120.0, duration_seconds=10.0, beats=[], sections=[])
    segs = build_segments(struct, [], -5.0)
    assert segs == []


def test_full_coverage_one_section_with_matching_rule() -> None:
    struct = _structure((0.0, 10.0, "chorus"))
    segs = build_segments(struct, [_rule("chorus", opacity=0.7, blend="screen")], 10.0)
    assert _covers(segs, 10.0)
    assert len(segs) == 1
    assert segs[0].opacity == pytest.approx(0.7)
    assert segs[0].blend == "screen"


def test_unmatched_section_label_uses_defaults() -> None:
    struct = _structure((0.0, 10.0, "bridge"))
    segs = build_segments(struct, [_rule("chorus", opacity=0.5)], 10.0)
    assert len(segs) == 1
    assert segs[0].opacity == pytest.approx(1.0)
    assert segs[0].blend == "normal"


# ---------------------------------------------------------------------------
# Gap filling
# ---------------------------------------------------------------------------


def test_gap_before_first_section_filled_with_defaults() -> None:
    struct = _structure((5.0, 10.0, "chorus"))
    segs = build_segments(struct, [_rule("chorus", opacity=0.8)], 10.0)
    assert _covers(segs, 10.0)
    assert segs[0] == Segment(start=0.0, end=5.0, opacity=1.0, blend="normal")
    assert segs[1].opacity == pytest.approx(0.8)


def test_gap_between_sections_filled_with_defaults() -> None:
    struct = _structure((0.0, 3.0, "intro"), (7.0, 10.0, "outro"))
    segs = build_segments(
        struct,
        [_rule("intro", opacity=0.5), _rule("outro", opacity=0.9)],
        10.0,
    )
    assert _covers(segs, 10.0)
    assert len(segs) == 3
    gap = segs[1]
    assert gap.start == pytest.approx(3.0)
    assert gap.end == pytest.approx(7.0)
    assert gap.opacity == pytest.approx(1.0)
    assert gap.blend == "normal"


def test_gap_after_last_section_filled_with_defaults() -> None:
    struct = _structure((0.0, 5.0, "verse"))
    segs = build_segments(struct, [_rule("verse", opacity=0.6)], 10.0)
    assert _covers(segs, 10.0)
    tail = segs[-1]
    assert tail.start == pytest.approx(5.0)
    assert tail.end == pytest.approx(10.0)
    assert tail.opacity == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Clipping to duration
# ---------------------------------------------------------------------------


def test_section_extending_beyond_duration_is_clipped() -> None:
    struct = _structure((0.0, 30.0, "chorus"))
    segs = build_segments(struct, [_rule("chorus", opacity=0.5)], 10.0)
    assert _covers(segs, 10.0)
    assert segs[0].end == pytest.approx(10.0)


def test_section_starting_at_or_after_duration_is_ignored() -> None:
    struct = _structure((0.0, 5.0, "verse"), (10.0, 20.0, "chorus"))
    segs = build_segments(struct, [], 10.0)
    assert _covers(segs, 10.0)
    # "chorus" starts exactly at duration — should be dropped
    assert all(s.end <= 10.0 for s in segs)


# ---------------------------------------------------------------------------
# Multiple sections, various blend modes
# ---------------------------------------------------------------------------


def test_multiple_sections_different_blend_modes() -> None:
    struct = _structure((0.0, 4.0, "verse"), (4.0, 8.0, "chorus"), (8.0, 10.0, "outro"))
    rules = [
        _rule("verse", opacity=0.5, blend="multiply"),
        _rule("chorus", opacity=0.9, blend="screen"),
        _rule("outro", opacity=1.0, blend="add"),
    ]
    segs = build_segments(struct, rules, 10.0)
    assert _covers(segs, 10.0)
    assert len(segs) == 3
    assert segs[0].blend == "multiply"
    assert segs[1].blend == "screen"
    assert segs[2].blend == "add"


def test_empty_rules_all_defaults() -> None:
    struct = _structure((0.0, 5.0, "verse"), (5.0, 10.0, "chorus"))
    segs = build_segments(struct, [], 10.0)
    assert all(s.opacity == pytest.approx(1.0) for s in segs)
    assert all(s.blend == "normal" for s in segs)


# ---------------------------------------------------------------------------
# Boundary precision
# ---------------------------------------------------------------------------


def test_segment_boundaries_are_exact() -> None:
    struct = _structure((0.0, 3.5, "intro"), (3.5, 10.0, "outro"))
    segs = build_segments(struct, [], 10.0)
    assert segs[0].end == pytest.approx(3.5)
    assert segs[1].start == pytest.approx(3.5)


def test_duration_shorter_than_song_truncates_correctly() -> None:
    struct = _structure((0.0, 5.0, "verse"), (5.0, 10.0, "chorus"))
    segs = build_segments(struct, [_rule("chorus", opacity=0.8)], 7.0)
    assert _covers(segs, 7.0)
    # "chorus" should be clipped to end at 7.0
    chorus_seg = next(s for s in segs if s.opacity == pytest.approx(0.8))
    assert chorus_seg.end == pytest.approx(7.0)
