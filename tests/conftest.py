"""Shared test fixtures."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _write_wav(path: Path, y: np.ndarray, sr: int) -> None:
    """Write a mono float32 array as a 16-bit WAV file (stdlib only)."""
    samples = np.clip(y * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(samples.tobytes())


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    """Synthesize a short WAV file with a clear rhythmic structure (10 s, 120 BPM)."""
    sr = 22050
    duration = 10.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    # 440 Hz carrier + rhythmic emphasis at 2 Hz (120 BPM)
    y = 0.6 * np.sin(2 * np.pi * 440 * t)
    y += 0.4 * np.abs(np.sin(2 * np.pi * 2.0 * t))
    y = (y / np.max(np.abs(y)) * 0.8).astype(np.float32)

    path = tmp_path / "sample.wav"
    _write_wav(path, y, sr)
    return path
