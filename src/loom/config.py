"""TOML config loader and validator for Loom."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_BLEND_MODES = {
    "normal",
    "screen",
    "overlay",
    "multiply",
    "add",
    "subtract",
    "lighten",
    "darken",
    "hardlight",
    "softlight",
}


class ConfigError(ValueError):
    """Raised when a config file is invalid."""


@dataclass
class PathsConfig:
    base: Path
    song: Path
    output: Path
    overlays: list[Path] = field(default_factory=list)


@dataclass
class RenderConfig:
    width: int = 512
    height: int = 512
    fps: int = 24
    enable_diffusion: bool = False


@dataclass
class ScheduleConfig:
    deadline: str = "05:30"


@dataclass
class TimelineRule:
    section: str
    opacity: float = 1.0
    blend: str = "normal"


@dataclass
class LoomConfig:
    name: str
    paths: PathsConfig
    render: RenderConfig
    schedule: ScheduleConfig
    timeline: list[TimelineRule]


def _require(mapping: dict, key: str, context: str) -> object:
    if key not in mapping:
        raise ConfigError(f"[{context}] missing required field: '{key}'")
    return mapping[key]


def _parse_paths(raw: dict) -> PathsConfig:
    ctx = "paths"
    base = Path(str(_require(raw, "base", ctx)))
    song = Path(str(_require(raw, "song", ctx)))
    output = Path(str(_require(raw, "output", ctx)))

    raw_overlays = raw.get("overlays", [])
    if not isinstance(raw_overlays, list):
        raise ConfigError("[paths] 'overlays' must be a list of paths")
    overlays = [Path(str(p)) for p in raw_overlays]

    return PathsConfig(base=base, song=song, output=output, overlays=overlays)


def _parse_render(raw: dict) -> RenderConfig:
    ctx = "render"
    cfg = RenderConfig()

    if "width" in raw:
        v = raw["width"]
        if not isinstance(v, int) or v <= 0:
            raise ConfigError(f"[{ctx}] 'width' must be a positive integer")
        cfg.width = v

    if "height" in raw:
        v = raw["height"]
        if not isinstance(v, int) or v <= 0:
            raise ConfigError(f"[{ctx}] 'height' must be a positive integer")
        cfg.height = v

    if "fps" in raw:
        v = raw["fps"]
        if not isinstance(v, int) or v <= 0:
            raise ConfigError(f"[{ctx}] 'fps' must be a positive integer")
        cfg.fps = v

    if "enable_diffusion" in raw:
        v = raw["enable_diffusion"]
        if not isinstance(v, bool):
            raise ConfigError(f"[{ctx}] 'enable_diffusion' must be a boolean")
        cfg.enable_diffusion = v

    return cfg


def _parse_schedule(raw: dict) -> ScheduleConfig:
    cfg = ScheduleConfig()

    if "deadline" in raw:
        v = raw["deadline"]
        if not isinstance(v, str):
            raise ConfigError("[schedule] 'deadline' must be a string (e.g. '05:30')")
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ConfigError(f"[schedule] 'deadline' must be HH:MM format, got: {v!r}")
        cfg.deadline = v

    return cfg


def _parse_timeline(raw_list: list[dict]) -> list[TimelineRule]:
    rules: list[TimelineRule] = []
    for i, raw in enumerate(raw_list):
        ctx = f"timeline[{i}]"
        section = str(_require(raw, "section", ctx))

        opacity = raw.get("opacity", 1.0)
        if not isinstance(opacity, (int, float)) or not (0.0 <= float(opacity) <= 1.0):
            raise ConfigError(f"[{ctx}] 'opacity' must be a float in [0.0, 1.0]")

        blend = raw.get("blend", "normal")
        if blend not in VALID_BLEND_MODES:
            raise ConfigError(f"[{ctx}] 'blend' must be one of: {sorted(VALID_BLEND_MODES)}")

        rules.append(TimelineRule(section=section, opacity=float(opacity), blend=blend))

    return rules


def load_config(path: Path) -> LoomConfig:
    """Load and validate a Loom TOML config file.

    Raises:
        ConfigError: if the file is missing, unreadable, or invalid.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML parse error in {path}: {exc}") from exc

    # [project]
    project = raw.get("project", {})
    name = str(_require(project, "name", "project"))

    # [paths]
    if "paths" not in raw:
        raise ConfigError("Missing required section: [paths]")
    paths = _parse_paths(raw["paths"])

    # [render]
    render = _parse_render(raw.get("render", {}))

    # [schedule]
    schedule = _parse_schedule(raw.get("schedule", {}))

    # [[timeline]]
    timeline = _parse_timeline(raw.get("timeline", []))

    cfg = LoomConfig(
        name=name,
        paths=paths,
        render=render,
        schedule=schedule,
        timeline=timeline,
    )
    logger.debug("Loaded config: project=%r deadline=%s", name, schedule.deadline)
    return cfg
