"""Tests for loom.config — TOML loader and validator."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from loom.config import ConfigError, LoomConfig, load_config


def write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "loom.toml"
    p.write_text(textwrap.dedent(content))
    return p


MINIMAL_VALID = """\
    [project]
    name = "test-project"

    [paths]
    base = "input/base.mp4"
    song = "input/song.mp3"
    output = "output/"
    """


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_minimal_config_loads(tmp_path: Path) -> None:
    cfg = load_config(write_toml(tmp_path, MINIMAL_VALID))
    assert isinstance(cfg, LoomConfig)
    assert cfg.name == "test-project"
    assert cfg.paths.base == Path("input/base.mp4")
    assert cfg.paths.song == Path("input/song.mp3")
    assert cfg.paths.output == Path("output/")
    assert cfg.paths.overlays == []


def test_defaults_applied(tmp_path: Path) -> None:
    cfg = load_config(write_toml(tmp_path, MINIMAL_VALID))
    assert cfg.render.width == 512
    assert cfg.render.height == 512
    assert cfg.render.fps == 24
    assert cfg.render.enable_diffusion is False
    assert cfg.schedule.deadline == "05:30"
    assert cfg.timeline == []


def test_full_config_loads(tmp_path: Path) -> None:
    toml = """\
        [project]
        name = "full-project"

        [paths]
        base = "input/base.mp4"
        song = "input/song.mp3"
        output = "output/"
        overlays = ["input/ov1.mp4", "input/ov2.mp4"]

        [render]
        width = 1920
        height = 1080
        fps = 30
        enable_diffusion = true

        [schedule]
        deadline = "04:00"

        [[timeline]]
        section = "chorus"
        opacity = 0.7
        blend = "screen"

        [[timeline]]
        section = "verse"
        opacity = 0.4
        blend = "overlay"
        """
    cfg = load_config(write_toml(tmp_path, toml))
    assert cfg.name == "full-project"
    assert cfg.render.width == 1920
    assert cfg.render.fps == 30
    assert cfg.render.enable_diffusion is True
    assert cfg.schedule.deadline == "04:00"
    assert len(cfg.paths.overlays) == 2
    assert len(cfg.timeline) == 2
    assert cfg.timeline[0].section == "chorus"
    assert cfg.timeline[0].opacity == 0.7
    assert cfg.timeline[0].blend == "screen"


def test_timeline_defaults(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + '\n[[timeline]]\nsection = "intro"\n'
    cfg = load_config(write_toml(tmp_path, toml))
    assert cfg.timeline[0].opacity == 1.0
    assert cfg.timeline[0].blend == "normal"


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")


def test_invalid_toml_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.toml"
    p.write_text("this is not [ valid toml ===")
    with pytest.raises(ConfigError, match="TOML parse error"):
        load_config(p)


def test_missing_project_name(tmp_path: Path) -> None:
    toml = """\
        [project]

        [paths]
        base = "b.mp4"
        song = "s.mp3"
        output = "out/"
        """
    with pytest.raises(ConfigError, match="missing required field.*name"):
        load_config(write_toml(tmp_path, toml))


def test_missing_paths_section(tmp_path: Path) -> None:
    toml = """\
        [project]
        name = "x"
        """
    with pytest.raises(ConfigError, match="Missing required section.*paths"):
        load_config(write_toml(tmp_path, toml))


def test_missing_paths_base(tmp_path: Path) -> None:
    toml = """\
        [project]
        name = "x"

        [paths]
        song = "s.mp3"
        output = "out/"
        """
    with pytest.raises(ConfigError, match="missing required field.*base"):
        load_config(write_toml(tmp_path, toml))


def test_missing_paths_song(tmp_path: Path) -> None:
    toml = """\
        [project]
        name = "x"

        [paths]
        base = "b.mp4"
        output = "out/"
        """
    with pytest.raises(ConfigError, match="missing required field.*song"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_render_width(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + "\n[render]\nwidth = -1\n"
    with pytest.raises(ConfigError, match="'width' must be a positive integer"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_render_fps(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + "\n[render]\nfps = 0\n"
    with pytest.raises(ConfigError, match="'fps' must be a positive integer"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_deadline_format(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + '\n[schedule]\ndeadline = "5:30pm"\n'
    with pytest.raises(ConfigError, match="HH:MM format"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_timeline_opacity(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + '\n[[timeline]]\nsection = "chorus"\nopacity = 1.5\n'
    with pytest.raises(ConfigError, match="'opacity' must be a float in"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_timeline_blend(tmp_path: Path) -> None:
    toml = MINIMAL_VALID + '\n[[timeline]]\nsection = "chorus"\nblend = "warp"\n'
    with pytest.raises(ConfigError, match="'blend' must be one of"):
        load_config(write_toml(tmp_path, toml))


def test_invalid_overlays_not_list(tmp_path: Path) -> None:
    toml = """\
        [project]
        name = "x"

        [paths]
        base = "b.mp4"
        song = "s.mp3"
        output = "out/"
        overlays = "single-string"
        """
    with pytest.raises(ConfigError, match="'overlays' must be a list"):
        load_config(write_toml(tmp_path, toml))
