# Scheduling

Loom runs unattended via a systemd user timer in the 00:00–05:30 window.
Ollama is stopped before the render and restarted on exit.

## Requirements

- systemd user session enabled (`loginctl enable-linger $USER` if not already set)
- `loom` binary on `$PATH` (installed via `pip install -e .` or equivalent)
- `loom.toml` present in the project directory
- ComfyUI running at the expected URL before the timer fires (manage separately)

## Generate unit files

```bash
loom install-systemd --project-dir /path/to/loom-project
```

By default the unit files are written to the project directory. Use `--output-dir`
to write them elsewhere:

```bash
loom install-systemd --project-dir /path/to/loom-project --output-dir /tmp/loom-units
```

The command prints the generated paths and installation instructions.

## Install

```bash
mkdir -p ~/.config/systemd/user/
cp /path/to/loom-project/loom.service \
   /path/to/loom-project/loom.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loom.timer
```

## Verification

```bash
# Check timer status and next trigger time
systemctl --user status loom.timer
systemctl --user list-timers loom.timer

# Inspect most recent service run
systemctl --user status loom.service
journalctl --user -u loom.service -n 50
```

## Deadline

The service unit passes `LOOM_DEADLINE` (default `05:30`) to the pipeline.
Override in `loom.toml` under `[schedule]`:

```toml
[schedule]
deadline = "05:30"
```

Or set the env var before the service starts. The pipeline stops cleanly when the
deadline is reached and saves state so the next night's run can resume.

## Ollama coordination

The service unit stops `ollama.service` before running (`ExecStartPre`) and
restarts it on exit (`ExecStopPost`). Both directives use `-` (ignore failure)
so the render proceeds even if Ollama is not running.

## Morning-brief coordination

Morning-brief's timer must be moved from 04:15 to 05:30 so it does not start
while loom is still holding the GPU. Edit
`~/.config/systemd/user/morning-brief.timer` (or the equivalent in the
morning-brief project) and change `OnCalendar` to `*-*-* 05:30:00`, then:

```bash
systemctl --user daemon-reload
systemctl --user restart morning-brief.timer
```

This is a **paired deploy** with loom's systemd rollout — both changes must be
applied together.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Timer never fires | `systemctl --user is-enabled loom.timer` → must be `enabled` |
| Service exits immediately | `journalctl --user -u loom.service` for the error |
| ComfyUI not reachable | Ensure ComfyUI starts before 00:00; loom does not manage it |
| OOM during render | Reduce `context_length` or `batch_size` in config |
| Deadline reached mid-render | State is saved; next night resumes automatically |
| Ollama not restarting | Check `ExecStopPost` in the service unit; verify ollama.service exists |
