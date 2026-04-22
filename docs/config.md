# Loom Config Reference

Config files use [TOML](https://toml.io/). Pass them to any subcommand via `--config path/to/loom.toml`.

---

## Minimal example

```toml
[project]
name = "my-video"

[paths]
base   = "input/base.mp4"
song   = "input/song.mp3"
output = "output/"
```

## Full example

```toml
[project]
name = "my-video"

[paths]
base     = "input/base.mp4"
song     = "input/song.mp3"
output   = "output/"
overlays = ["input/overlays/fx1.mp4", "input/overlays/fx2.mp4"]

[render]
width             = 512
height            = 512
fps               = 24
enable_diffusion  = false

[schedule]
deadline = "05:30"

[[timeline]]
section = "intro"
opacity = 0.5
blend   = "normal"

[[timeline]]
section = "chorus"
opacity = 0.8
blend   = "screen"
```

---

## Sections

### `[project]`

| Key    | Type   | Required | Description          |
|--------|--------|----------|----------------------|
| `name` | string | yes      | Human-readable label |

---

### `[paths]`

All paths may be relative (resolved from the working directory at runtime) or absolute.

| Key        | Type           | Required | Description                                   |
|------------|----------------|----------|-----------------------------------------------|
| `base`     | string (path)  | yes      | Base footage file (mp4 / mov)                 |
| `song`     | string (path)  | yes      | Audio file fed to librosa for analysis        |
| `output`   | string (path)  | yes      | Directory where renders are written           |
| `overlays` | list of paths  | no       | Overlay clips processed by the preprocess stage |

---

### `[render]`

| Key                | Type    | Default | Description                                            |
|--------------------|---------|---------|--------------------------------------------------------|
| `width`            | int     | `512`   | Diffusion/preprocess render width (px). Keep ≤ 512 for RTX 2060. |
| `height`           | int     | `512`   | Diffusion/preprocess render height (px).               |
| `fps`              | int     | `24`    | Output frame rate.                                     |
| `enable_diffusion` | bool    | `false` | Run AnimateDiff + ControlNet stylization stage.        |

---

### `[schedule]`

| Key        | Type   | Default  | Description                                          |
|------------|--------|----------|------------------------------------------------------|
| `deadline` | string | `"05:30"` | Hard stop time (HH:MM, 24-hour local). Pipeline exits before this. Override with `LOOM_DEADLINE` env var. |

---

### `[[timeline]]`

Repeatable. Each entry maps a song section label (from the analysis JSON) to compositing parameters applied for the duration of that section.

| Key       | Type   | Default    | Description                                                  |
|-----------|--------|------------|--------------------------------------------------------------|
| `section` | string | (required) | Section label matching `{song}.structure.json` (e.g. `"chorus"`, `"verse"`, `"intro"`). |
| `opacity` | float  | `1.0`      | Overlay opacity, `0.0` (transparent) – `1.0` (opaque).      |
| `blend`   | string | `"normal"` | ffmpeg blend mode. See valid values below.                   |

#### Valid blend modes

`add`, `darken`, `hardlight`, `lighten`, `multiply`, `normal`, `overlay`, `screen`, `softlight`, `subtract`

---

## Environment overrides

| Variable         | Overrides             |
|------------------|-----------------------|
| `LOOM_DEADLINE`  | `schedule.deadline`   |
