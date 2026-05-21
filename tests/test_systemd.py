"""Tests for loom.systemd — unit file template rendering."""

from __future__ import annotations

import textwrap
from pathlib import Path

from loom.systemd import (
    generate_units,
    render_service,
    render_timer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cfg(tmp_path: Path, deadline: str = "05:30"):
    """Return a minimal LoomConfig for tests."""
    toml_text = textwrap.dedent(f"""\
        [project]
        name = "test-project"

        [paths]
        base = "input/base.mp4"
        song = "input/song.mp3"
        output = "output/"

        [schedule]
        deadline = "{deadline}"
    """)
    config_path = tmp_path / "loom.toml"
    config_path.write_text(toml_text)

    from loom.config import load_config  # noqa: PLC0415

    return load_config(config_path)


# ---------------------------------------------------------------------------
# render_service
# ---------------------------------------------------------------------------


def test_render_service_contains_project_name():
    text = render_service(
        project_name="my-video",
        loom_bin="/usr/local/bin/loom",
        config_path=Path("/projects/loom/loom.toml"),
        log_path=Path("/projects/loom/output/loom.log"),
        deadline="05:30",
    )
    assert "my-video" in text


def test_render_service_contains_loom_bin():
    text = render_service(
        project_name="p",
        loom_bin="/usr/local/bin/loom",
        config_path=Path("/cfg/loom.toml"),
        log_path=Path("/cfg/output/loom.log"),
        deadline="05:30",
    )
    assert "/usr/local/bin/loom" in text


def test_render_service_contains_config_path():
    text = render_service(
        project_name="p",
        loom_bin="loom",
        config_path=Path("/my/project/loom.toml"),
        log_path=Path("/my/project/output/loom.log"),
        deadline="05:30",
    )
    assert "/my/project/loom.toml" in text


def test_render_service_contains_deadline():
    text = render_service(
        project_name="p",
        loom_bin="loom",
        config_path=Path("/cfg/loom.toml"),
        log_path=Path("/cfg/output/loom.log"),
        deadline="04:45",
    )
    assert "04:45" in text


def test_render_service_contains_log_path():
    text = render_service(
        project_name="p",
        loom_bin="loom",
        config_path=Path("/cfg/loom.toml"),
        log_path=Path("/cfg/output/loom.log"),
        deadline="05:30",
    )
    assert "/cfg/output/loom.log" in text


def test_render_service_ollama_stop_start():
    text = render_service(
        project_name="p",
        loom_bin="loom",
        config_path=Path("/cfg/loom.toml"),
        log_path=Path("/cfg/output/loom.log"),
        deadline="05:30",
    )
    assert "ollama.service" in text
    assert "ExecStartPre" in text
    assert "ExecStopPost" in text


def test_render_service_no_unfilled_placeholders():
    text = render_service(
        project_name="my-project",
        loom_bin="/usr/bin/loom",
        config_path=Path("/p/loom.toml"),
        log_path=Path("/p/output/loom.log"),
        deadline="05:30",
    )
    assert "{{" not in text
    assert "}}" not in text


# ---------------------------------------------------------------------------
# render_timer
# ---------------------------------------------------------------------------


def test_render_timer_contains_project_name():
    text = render_timer(project_name="my-video")
    assert "my-video" in text


def test_render_timer_midnight_calendar():
    text = render_timer(project_name="p")
    assert "00:00:00" in text


def test_render_timer_no_unfilled_placeholders():
    text = render_timer(project_name="p")
    assert "{{" not in text
    assert "}}" not in text


def test_render_timer_has_install_section():
    text = render_timer(project_name="p")
    assert "[Install]" in text
    assert "timers.target" in text


# ---------------------------------------------------------------------------
# generate_units
# ---------------------------------------------------------------------------


def test_generate_units_creates_files(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    service_path, timer_path = generate_units(cfg, project_dir=tmp_path, output_dir=tmp_path)
    assert service_path.exists()
    assert timer_path.exists()
    assert service_path.name == "loom.service"
    assert timer_path.name == "loom.timer"


def test_generate_units_service_content(tmp_path: Path):
    cfg = make_cfg(tmp_path, deadline="05:30")
    service_path, _ = generate_units(cfg, project_dir=tmp_path, output_dir=tmp_path)
    text = service_path.read_text()
    assert "test-project" in text
    assert "05:30" in text
    assert "loom.toml" in text


def test_generate_units_timer_content(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    _, timer_path = generate_units(cfg, project_dir=tmp_path, output_dir=tmp_path)
    text = timer_path.read_text()
    assert "test-project" in text
    assert "00:00:00" in text


def test_generate_units_output_dir_created(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    out_dir = tmp_path / "units" / "nested"
    generate_units(cfg, project_dir=tmp_path, output_dir=out_dir)
    assert out_dir.is_dir()


def test_generate_units_deadline_override(tmp_path: Path):
    cfg = make_cfg(tmp_path, deadline="04:00")
    service_path, _ = generate_units(cfg, project_dir=tmp_path, output_dir=tmp_path)
    assert "04:00" in service_path.read_text()
