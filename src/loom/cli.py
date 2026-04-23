"""Loom CLI entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from loom import __version__
from loom.config import ConfigError, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def cli(debug: bool) -> None:
    """Loom — overnight music video renderer."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
def version() -> None:
    """Print package version and exit."""
    click.echo(f"loom {__version__}")


@cli.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to project TOML config file.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Validate config only, no rendering.")
def run(config_path: Path, dry_run: bool) -> None:
    """Run the full render pipeline."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("Loaded config: project=%r deadline=%s", cfg.name, cfg.schedule.deadline)

    if dry_run:
        logger.info("Dry run — pipeline not started.")
        return

    logger.info("Pipeline not yet implemented.")


@cli.command()
@click.option(
    "--song",
    "song_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Path to audio file (MP3, WAV, M4A).",
)
@click.option("--force", is_flag=True, default=False, help="Re-analyze even if cached JSON exists.")
def analyze(song_path: Path, force: bool) -> None:
    """Analyze song structure and write structure JSON beside the source file."""
    from loom.analysis import analyze_song  # noqa: PLC0415

    try:
        out = analyze_song(song_path, force=force)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        sys.exit(1)

    logger.info("Structure written: %s", out)


@cli.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to project TOML config file.",
)
def preprocess(config_path: Path) -> None:
    """Run ComfyUI preprocessing (lineart, canny, depth, hed) on overlays."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("preprocess: %d overlay(s) (not yet implemented)", len(cfg.paths.overlays))


@cli.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to project TOML config file.",
)
def composite(config_path: Path) -> None:
    """Composite base footage and overlays with ffmpeg."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("composite: output=%s (not yet implemented)", cfg.paths.output)


@cli.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to project TOML config file.",
)
def stylize(config_path: Path) -> None:
    """Run AnimateDiff + ControlNet stylization via ComfyUI (optional)."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    if not cfg.render.enable_diffusion:
        logger.info("stylize: enable_diffusion=false, skipping.")
        return

    logger.info("stylize: not yet implemented")


@cli.command("install-systemd")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to project TOML config file.",
)
def install_systemd(config_path: Path) -> None:
    """Install systemd user service and timer units."""
    try:
        load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("install-systemd: not yet implemented")
