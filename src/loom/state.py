"""Persistent pipeline state for resumability.

State is written to ``output/.loom-state.json``.  Each stage records its
completion status and an optional hash of its primary input so that a re-run
can detect when an input has changed and the stage must be repeated.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILENAME = ".loom-state.json"
_READ_CHUNK = 65536  # 64 KiB — enough for a quick "has this file changed?" hash


class PipelineState:
    """Reads and writes the ``.loom-state.json`` resumability file."""

    def __init__(self, output_dir: Path) -> None:
        self._path = output_dir / _STATE_FILENAME
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read state file %s: %s — starting fresh", self._path, exc)
        return {"stages": {}}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))
        logger.debug("State saved: %s", self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_complete(self, stage: str, input_hash: str | None = None) -> bool:
        """Return True if *stage* completed successfully with a matching input hash.

        If *input_hash* is None the hash is not checked — any completed entry
        qualifies.
        """
        entry = self._data.get("stages", {}).get(stage)
        if not entry or entry.get("status") != "completed":
            return False
        if input_hash is not None and entry.get("input_hash") != input_hash:
            return False
        return True

    def mark_complete(self, stage: str, input_hash: str | None = None) -> None:
        """Record *stage* as successfully completed."""
        self._data.setdefault("stages", {})[stage] = {
            "status": "completed",
            "input_hash": input_hash,
        }
        self._save()
        logger.debug("Stage %r marked complete", stage)

    def mark_failed(self, stage: str) -> None:
        """Record *stage* as failed (for observability; does not block re-runs)."""
        self._data.setdefault("stages", {})[stage] = {"status": "failed"}
        self._save()
        logger.debug("Stage %r marked failed", stage)

    def clear(self, stage: str | None = None) -> None:
        """Clear state for *stage*, or all stages when *stage* is None."""
        if stage is None:
            self._data["stages"] = {}
        else:
            self._data.get("stages", {}).pop(stage, None)
        self._save()


def file_hash(path: Path) -> str:
    """Return a hex SHA-256 digest of the first 64 KiB of *path*.

    Used as a quick change-detection fingerprint; not a cryptographic
    guarantee of full-file integrity.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(_READ_CHUNK))
    return h.hexdigest()
