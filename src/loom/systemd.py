"""Unit file templating for systemd integration."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Templates directory is at project root (two levels up from this file's package)
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def _render(template_text: str, **kwargs: object) -> str:
    """Substitute {{ var }} placeholders in *template_text* with *kwargs* values."""

    def replace(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1).strip()
        if key not in kwargs:
            raise KeyError(f"Template variable not found: {key!r}")
        return str(kwargs[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template_text)


def _read_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text()


def render_service(
    *,
    project_name: str,
    loom_bin: str,
    config_path: Path,
    log_path: Path,
    deadline: str,
) -> str:
    """Render the service unit template and return the filled text."""
    return _render(
        _read_template("loom.service.j2"),
        project_name=project_name,
        loom_bin=loom_bin,
        config_path=config_path,
        log_path=log_path,
        deadline=deadline,
    )


def render_timer(*, project_name: str) -> str:
    """Render the timer unit template and return the filled text."""
    return _render(
        _read_template("loom.timer.j2"),
        project_name=project_name,
    )


def generate_units(
    cfg: object,
    project_dir: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Render service + timer unit files and write them to *output_dir*.

    Args:
        cfg: Loaded :class:`~loom.config.LoomConfig` instance.
        project_dir: Absolute path to the loom project directory.
        output_dir: Directory where unit files will be written.

    Returns:
        Tuple of ``(service_path, timer_path)``.
    """
    from loom.config import LoomConfig  # noqa: PLC0415

    assert isinstance(cfg, LoomConfig)

    loom_bin = shutil.which("loom") or "loom"
    config_path = (project_dir / "loom.toml").resolve()

    output_root = cfg.paths.output
    if not output_root.is_absolute():
        output_root = (project_dir / output_root).resolve()
    log_path = output_root / "loom.log"

    # systemd opens StandardOutput=append: BEFORE running ExecStartPre, so the
    # log's parent dir must already exist or the unit dies with 209/STDOUT (an
    # ExecStartPre mkdir would be too late). Create it at install time.
    output_root.mkdir(parents=True, exist_ok=True)

    service_text = render_service(
        project_name=cfg.name,
        loom_bin=loom_bin,
        config_path=config_path,
        log_path=log_path,
        deadline=cfg.schedule.deadline,
    )
    timer_text = render_timer(project_name=cfg.name)

    output_dir.mkdir(parents=True, exist_ok=True)
    service_path = output_dir / "loom.service"
    timer_path = output_dir / "loom.timer"
    service_path.write_text(service_text)
    timer_path.write_text(timer_text)

    logger.info("Generated: %s", service_path)
    logger.info("Generated: %s", timer_path)

    return service_path, timer_path
