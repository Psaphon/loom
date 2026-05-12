"""Loom CLI entry point."""

from __future__ import annotations

import logging
import os
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
@click.option(
    "--force", is_flag=True, default=False, help="Re-run all stages, ignoring saved state."
)
@click.option(
    "--deadline",
    default=None,
    envvar="LOOM_DEADLINE",
    help="Stop cleanly at HH:MM (overrides config and LOOM_DEADLINE env var).",
)
@click.option(
    "--comfy-url",
    default="http://127.0.0.1:8188",
    show_default=True,
    envvar="COMFY_URL",
    help="ComfyUI base URL.",
)
def run(
    config_path: Path,
    dry_run: bool,
    force: bool,
    deadline: str | None,
    comfy_url: str,
) -> None:
    """Run the full render pipeline (analyze → stylize → preprocess → composite → upscale → mux)."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("Loaded config: project=%r deadline=%s", cfg.name, cfg.schedule.deadline)

    if deadline is not None:
        # CLI/env deadline overrides config; expose via env so pipeline reads it
        os.environ["LOOM_DEADLINE"] = deadline

    if dry_run:
        logger.info("Dry run — pipeline not started.")
        return

    # Add a file handler so progress is also written to output/loom.log
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    log_file = cfg.paths.output / "loom.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    logging.getLogger().addHandler(file_handler)

    from loom.pipeline import DeadlineReached, PipelineError, run_pipeline  # noqa: PLC0415

    try:
        final = run_pipeline(cfg, force=force, comfy_url=comfy_url)
    except DeadlineReached as exc:
        logger.warning("Deadline reached: %s", exc)
        sys.exit(2)
    except PipelineError as exc:
        logger.error("Pipeline failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        sys.exit(1)
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()

    logger.info("Done: %s", final)


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
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Input overlay video file.",
)
@click.option(
    "--style",
    required=True,
    type=click.Choice(["lineart", "canny", "depth", "hed"]),
    help="Preprocessing style.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Output preprocessed video file.",
)
@click.option(
    "--comfy-url",
    default="http://127.0.0.1:8188",
    show_default=True,
    envvar="COMFY_URL",
    help="ComfyUI base URL.",
)
@click.option(
    "--batch-size",
    default=16,
    show_default=True,
    help="Number of frames to submit to ComfyUI concurrently.",
)
def preprocess(
    input_path: Path,
    style: str,
    output_path: Path,
    comfy_url: str,
    batch_size: int,
) -> None:
    """Preprocess an overlay video via ComfyUI (lineart, canny, depth, hed)."""
    import asyncio  # noqa: PLC0415

    from loom.preprocess import PreprocessError, preprocess_overlay  # noqa: PLC0415

    try:
        asyncio.run(
            preprocess_overlay(
                input_path,
                output_path,
                style,
                comfy_url=comfy_url,
                batch_size=batch_size,
            )
        )
    except (ValueError, PreprocessError) as exc:
        logger.error("Preprocessing failed: %s", exc)
        sys.exit(1)

    logger.info("Done: %s", output_path)


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
    from loom.composite import CompositeError, run_composite  # noqa: PLC0415

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    try:
        out = run_composite(cfg)
    except CompositeError as exc:
        logger.error("Composite failed: %s", exc)
        sys.exit(1)

    logger.info("Done: %s", out)


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Input base footage video file.",
)
@click.option(
    "--prompt",
    required=True,
    help='Positive text prompt describing the desired style (e.g. "watercolor painting").',
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Output stylized video file.",
)
@click.option(
    "--negative-prompt",
    default=None,
    show_default=False,
    help="Negative text prompt. Defaults to a generic quality filter.",
)
@click.option(
    "--comfy-url",
    default="http://127.0.0.1:8188",
    show_default=True,
    envvar="COMFY_URL",
    help="ComfyUI base URL.",
)
@click.option(
    "--context-length",
    default=16,
    show_default=True,
    help="Frames per AnimateDiff context window. Reduce to 8 if OOM.",
)
@click.option(
    "--width",
    default=512,
    show_default=True,
    help="Output width in pixels. Keep at 512 for 6 GB VRAM.",
)
@click.option(
    "--height",
    default=512,
    show_default=True,
    help="Output height in pixels. Keep at 512 for 6 GB VRAM.",
)
@click.option("--steps", default=20, show_default=True, help="KSampler denoising steps.")
@click.option("--cfg", default=7.0, show_default=True, help="Classifier-free guidance scale.")
@click.option("--seed", default=42, show_default=True, help="RNG seed.")
def stylize(
    input_path: Path,
    prompt: str,
    output_path: Path,
    negative_prompt: str | None,
    comfy_url: str,
    context_length: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> None:
    """Stylize base footage with AnimateDiff + ControlNet via ComfyUI."""
    import asyncio  # noqa: PLC0415

    from loom.stylize import OOMError, StylizeError, stylize_video  # noqa: PLC0415

    kwargs: dict = {}
    if negative_prompt is not None:
        kwargs["negative_prompt"] = negative_prompt

    try:
        asyncio.run(
            stylize_video(
                input_path,
                output_path,
                prompt,
                comfy_url=comfy_url,
                context_length=context_length,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                seed=seed,
                **kwargs,
            )
        )
    except OOMError as exc:
        logger.error("GPU out of memory: %s", exc)
        sys.exit(1)
    except (StylizeError, ValueError) as exc:
        logger.error("Stylization failed: %s", exc)
        sys.exit(1)

    logger.info("Done: %s", output_path)


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Input low-resolution video file.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Output upscaled video file.",
)
@click.option(
    "--target",
    default="1080p",
    show_default=True,
    type=click.Choice(["1080p", "4k"]),
    help="Target output resolution.",
)
@click.option(
    "--comfy-url",
    default="http://127.0.0.1:8188",
    show_default=True,
    envvar="COMFY_URL",
    help="ComfyUI base URL.",
)
def upscale(
    input_path: Path,
    output_path: Path,
    target: str,
    comfy_url: str,
) -> None:
    """Upscale a low-resolution video to 1080p or 4K via Real-ESRGAN in ComfyUI."""
    import asyncio  # noqa: PLC0415

    from loom.upscale import OOMError, UpscaleError, upscale_video  # noqa: PLC0415

    try:
        asyncio.run(
            upscale_video(
                input_path,
                output_path,
                target,
                comfy_url=comfy_url,
            )
        )
    except OOMError as exc:
        logger.error("GPU out of memory: %s", exc)
        sys.exit(1)
    except (UpscaleError, ValueError) as exc:
        logger.error("Upscale failed: %s", exc)
        sys.exit(1)

    logger.info("Done: %s", output_path)


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
