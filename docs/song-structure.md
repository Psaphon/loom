# Song Structure JSON

Reference for the `{song_name}.structure.json` file produced by `loom analyze`.

## Overview

Running `loom analyze --song path/to/song.mp3` writes a JSON file beside the
source audio:

```
path/to/song.structure.json
```

The file is human-readable and hand-editable. Downstream stages (composite,
timeline modulation) read it; they never re-run librosa.

## Schema

```json
{
  "tempo_bpm": 128.0,
  "duration_seconds": 213.456,
  "beats": [0.348, 0.814, 1.28, ...],
  "sections": [
    { "start": 0.0,    "end": 32.1,  "label": "section_0" },
    { "start": 32.1,   "end": 96.3,  "label": "section_1" },
    { "start": 96.3,   "end": 213.456, "label": "section_2" }
  ]
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `tempo_bpm` | float | Detected tempo in BPM |
| `duration_seconds` | float | Total audio duration |
| `beats` | list[float] | Beat onset times in seconds |
| `sections` | list[Section] | Structural segments (see below) |

### Section object

| Field | Type | Description |
|-------|------|-------------|
| `start` | float | Section start time in seconds |
| `end` | float | Section end time in seconds |
| `label` | string | Human-readable label (e.g. `"chorus"`, `"section_0"`) |

Section labels default to `section_0`, `section_1`, … — rename them freely.
The `composite` and `timeline` stages match on the `label` field.

## Caching

Analysis is skipped if the JSON exists and its mtime is ≥ the song file's
mtime. Use `--force` to override:

```bash
loom analyze --song song.mp3 --force
```

## Hand-editing

Common edits:

- **Rename sections** — change `"section_0"` to `"intro"`, `"chorus"`, etc.,
  then reference those names in `[[timeline]]` config blocks.
- **Split a section** — duplicate the entry and adjust `start`/`end` values.
  Ensure contiguous coverage (no gaps or overlaps).
- **Correct beats** — remove spurious entries or add missing ones; values are
  plain floats in seconds.
- **Fix tempo** — `tempo_bpm` is informational only in v1; edit freely.

## Supported formats

MP3, WAV, M4A. Detection is by file extension (case-insensitive).
