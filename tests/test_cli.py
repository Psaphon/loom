"""Tests for loom.cli — exit-code contract of the `run` command.

The process exit code is loom's contract with systemd: 0 = success, non-zero
= failed unit. These tests invoke the real Click command and assert the code,
which is the boundary that actually matters to the supervisor.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from loom.cli import cli
from loom.pipeline import DeadlineReached, PipelineError


def _write_config(tmp_path: Path) -> Path:
    """Write a minimal valid loom.toml with an absolute output dir under tmp."""
    config_path = tmp_path / "loom.toml"
    config_path.write_text(
        textwrap.dedent(f"""\
            [project]
            name = "test-project"

            [paths]
            base = "input/base.mp4"
            song = "input/song.mp3"
            output = "{tmp_path / "output"}"

            [schedule]
            deadline = "05:30"
        """)
    )
    return config_path


def test_run_exits_zero_on_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reached deadline is a clean stop — systemd must see success (exit 0)."""
    config_path = _write_config(tmp_path)

    def _raise_deadline(*_args: object, **_kwargs: object) -> Path:
        raise DeadlineReached("Deadline 05:30 reached before stage 'upscale'.")

    monkeypatch.setattr("loom.pipeline.run_pipeline", _raise_deadline)

    result = CliRunner().invoke(cli, ["run", "--config", str(config_path)])

    assert result.exit_code == 0, result.output


def test_run_exits_one_on_pipeline_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine pipeline failure must still surface as a failed unit (exit 1)."""
    config_path = _write_config(tmp_path)

    def _raise_pipeline_error(*_args: object, **_kwargs: object) -> Path:
        raise PipelineError("stage 'composite' failed after 2 retries")

    monkeypatch.setattr("loom.pipeline.run_pipeline", _raise_pipeline_error)

    result = CliRunner().invoke(cli, ["run", "--config", str(config_path)])

    assert result.exit_code == 1, result.output


def test_run_exits_zero_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A full render completes with exit 0, same as a clean deadline stop."""
    config_path = _write_config(tmp_path)

    monkeypatch.setattr(
        "loom.pipeline.run_pipeline",
        lambda *_a, **_k: tmp_path / "output" / "final.mp4",
    )

    result = CliRunner().invoke(cli, ["run", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
