"""Maps song structure sections to per-segment render parameters."""

from __future__ import annotations

from dataclasses import dataclass

from loom.config import TimelineRule
from loom.schemas import SongStructure


@dataclass
class Segment:
    """A contiguous time span with constant compositing parameters."""

    start: float  # seconds
    end: float  # seconds
    opacity: float
    blend: str


def build_segments(
    structure: SongStructure,
    rules: list[TimelineRule],
    duration: float,
) -> list[Segment]:
    """Map song structure sections to compositing segments covering [0, duration].

    Sections are matched to timeline rules by label. Gaps, overlaps, and
    unmatched sections use the defaults: opacity=1.0, blend="normal".

    Sections are processed in order of their start time. If a section extends
    beyond *duration* it is clipped; sections starting at or after *duration*
    are ignored.

    Args:
        structure: Song structure with section data.
        rules: Per-section timeline rules from TOML config.
        duration: Total compositing duration in seconds (usually base video length).

    Returns:
        Sorted, non-overlapping list of Segments covering [0, duration].
    """
    if duration <= 0:
        return []

    rule_map: dict[str, TimelineRule] = {r.section: r for r in rules}
    sections_sorted = sorted(structure.sections, key=lambda s: s.start)

    segments: list[Segment] = []
    cursor = 0.0

    for sec in sections_sorted:
        if sec.start >= duration:
            break

        # Fill any gap before this section with defaults
        gap_end = min(sec.start, duration)
        if gap_end > cursor:
            segments.append(Segment(start=cursor, end=gap_end, opacity=1.0, blend="normal"))

        # Add this section, clipped to [cursor, duration]
        seg_start = max(sec.start, cursor)
        seg_end = min(sec.end, duration)

        if seg_end > seg_start:
            rule = rule_map.get(sec.label)
            segments.append(
                Segment(
                    start=seg_start,
                    end=seg_end,
                    opacity=rule.opacity if rule else 1.0,
                    blend=rule.blend if rule else "normal",
                )
            )

        cursor = max(cursor, seg_end)

    # Fill remaining tail with defaults
    if cursor < duration:
        segments.append(Segment(start=cursor, end=duration, opacity=1.0, blend="normal"))

    return segments
