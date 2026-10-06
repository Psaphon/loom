"""Dataclasses for song structure data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Beat:
    """A single beat timestamp in seconds."""

    time: float


@dataclass
class Section:
    """A structural section of a song."""

    start: float
    end: float
    label: str


@dataclass
class SongStructure:
    """Full song structure as produced by analysis and stored in JSON."""

    tempo_bpm: float
    duration_seconds: float
    beats: list[Beat] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: Path) -> SongStructure:
        """Load a SongStructure from a structure JSON file."""
        data = json.loads(path.read_text())
        return cls(
            tempo_bpm=data["tempo_bpm"],
            duration_seconds=data["duration_seconds"],
            beats=[Beat(time=t) for t in data["beats"]],
            sections=[Section(**s) for s in data["sections"]],
        )
