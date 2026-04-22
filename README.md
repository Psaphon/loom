# Loom

Overnight music video renderer. Orchestrates ComfyUI and ffmpeg to produce a composited,
upscaled music video from base footage, overlay clips, and a song — unattended, nightly.

> **Status:** early development. See `docs/DEVPLAN.md`.

## Quick start

```bash
pip install -e ".[dev]"
loom --help
loom version
loom run --config path/to/loom.toml --dry-run
```

## Config

See [`docs/config.md`](docs/config.md) for the full TOML schema.

## Development

```bash
ruff check . && ruff format --check .
pytest
```
