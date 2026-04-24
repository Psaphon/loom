"""Song structure analysis using librosa.

Produces a hand-editable JSON beside the source audio file.
librosa's first import is slow (numba warmup) — acceptable since analysis runs once per song.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".mp3", ".wav", ".m4a"}


def structure_path(song: Path) -> Path:
    """Return the path to the structure JSON (beside the source file)."""
    return song.parent / f"{song.stem}.structure.json"


def analyze_song(song: Path, force: bool = False) -> Path:
    """Analyze a song and write ``{stem}.structure.json`` beside it.

    Skips analysis if the JSON already exists and is newer than the song,
    unless *force* is True.

    Returns the path to the written JSON file.
    """
    if not song.exists():
        raise FileNotFoundError(f"Song file not found: {song}")

    if song.suffix.lower() not in SUPPORTED_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise ValueError(f"Unsupported format {song.suffix!r}. Supported: {supported}")

    out = structure_path(song)

    if not force and out.exists():
        if out.stat().st_mtime >= song.stat().st_mtime:
            logger.info("Cache hit — skipping analysis: %s", out)
            return out
        logger.info("Song modified since last analysis — re-analyzing.")

    logger.info("Analyzing: %s", song)

    # Lazy import: librosa pulls in numba which is slow to initialise.
    import librosa  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    y, sr = librosa.load(str(song), sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Tempo and beat positions
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo_bpm = float(np.atleast_1d(tempo)[0])
    beat_times = [round(float(t), 4) for t in librosa.frames_to_time(beat_frames, sr=sr)]

    # Section detection
    sections = _detect_sections(y, sr, duration)

    structure = {
        "tempo_bpm": round(tempo_bpm, 2),
        "duration_seconds": round(duration, 3),
        "beats": beat_times,
        "sections": sections,
    }

    out.write_text(json.dumps(structure, indent=2))
    logger.info("Wrote: %s", out)
    return out


def _detect_sections(y, sr: int, duration: float) -> list[dict]:
    """Detect structural sections via MFCC + agglomerative segmentation."""
    import librosa  # noqa: PLC0415

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=12)

    # Aim for roughly one segment per 30 seconds, clamped to [2, 10].
    n_segments = max(2, min(10, int(duration / 30)))

    bounds = librosa.segment.agglomerative(mfcc, k=n_segments)
    bound_times = librosa.frames_to_time(bounds, sr=sr)

    starts = [0.0, *bound_times.tolist()]
    ends = [*bound_times.tolist(), round(duration, 3)]

    raw = [
        (round(float(start), 3), round(float(end), 3))
        for start, end in zip(starts, ends, strict=True)
    ]
    # Drop zero-length segments (can occur on very short or uniform audio).
    non_empty = [(s, e) for s, e in raw if e > s]
    if not non_empty:
        non_empty = [(0.0, round(duration, 3))]

    return [{"start": s, "end": e, "label": f"section_{i}"} for i, (s, e) in enumerate(non_empty)]
