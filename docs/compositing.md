# Compositing Reference

The `loom composite` command layers preprocessed overlay clips onto base footage using ffmpeg's `blend` filter. Opacity and blend mode are driven by song structure sections configured in `[[timeline]]` TOML blocks.

---

## Usage

```
loom composite --config project.toml
```

Produces `output/composite.mp4` (video only, no audio). Requires:
- `paths.base` — base footage file
- `paths.song` + `{song_stem}.structure.json` beside it (run `loom analyze` first)
- `paths.overlays` — list of preprocessed overlay clips (optional)

---

## Timeline configuration

Each `[[timeline]]` entry maps a song section label to compositing parameters:

```toml
[[timeline]]
section = "intro"
opacity = 0.5
blend   = "normal"

[[timeline]]
section = "chorus"
opacity = 0.9
blend   = "screen"
```

Sections not covered by a rule default to `opacity = 1.0`, `blend = "normal"`.
Gaps between sections (time ranges not belonging to any section in the structure
JSON) also use these defaults.

---

## Blend modes

All modes apply opacity mixing: `result = blend(overlay, base) * opacity + base * (1 - opacity)`

| Mode       | Formula (per channel, values in [0, 255])                                    | Notes                                       |
|------------|------------------------------------------------------------------------------|---------------------------------------------|
| `normal`   | `overlay * opacity + base * (1 - opacity)`                                   | Standard alpha composite                    |
| `add`      | `min(overlay + base, 255) * opacity + base * (1 - opacity)`                  | Brightens; good for glow, light leaks       |
| `multiply` | `(overlay * base / 255) * opacity + base * (1 - opacity)`                   | Darkens; dark overlay areas knock out base  |
| `screen`   | `(255 - (255 - overlay) * (255 - base) / 255) * opacity + base * (1 - opacity)` | Inverse multiply; good for bright lines  |
| `overlay`  | Hard-light inversion: dark base → multiply, light base → screen              | High contrast; exaggerates mid-tones        |
| `lighten`  | `max(overlay, base) * opacity + base * (1 - opacity)`                        | Keeps lighter of the two pixels             |
| `darken`   | `min(overlay, base) * opacity + base * (1 - opacity)`                        | Keeps darker of the two pixels              |
| `subtract` | `max(base - overlay, 0) * opacity + base * (1 - opacity)`                    | Darkens base by overlay value               |
| `hardlight`| Like overlay but driven by overlay brightness instead of base                | Controls mix from the overlay side          |
| `softlight`| Pegtop softlight approximation                                               | Subtle contrast; similar to overlay, softer |

### Mode selection guide

- **`screen`** — Bright overlay elements (lineart, canny edges) over video. The most useful for preprocessed overlay clips.
- **`add`** — Light effects, particles, glows. Saturates quickly at full opacity; keep `opacity ≤ 0.5` for subtle results.
- **`multiply`** — Texture or darkening passes. Dark overlay areas darken the base; white areas are invisible.
- **`overlay`** — Contrast enhancement or grunge texture. Unintuitive results at high opacity; start at `opacity = 0.3`.
- **`normal`** — Simple alpha blend. Use when the overlay already has correct tone mapping.

---

## Overlay duration handling

Controlled by `render.overlay_loop` (default: `true`).

| Overlay vs base | `overlay_loop = true`      | `overlay_loop = false`          |
|-----------------|----------------------------|---------------------------------|
| Shorter         | Loops from start           | Holds last frame                |
| Same length     | No adjustment              | No adjustment                   |
| Longer          | Trimmed to base duration   | Trimmed to base duration        |

```toml
[render]
overlay_loop = false   # hold last frame instead of looping
```

---

## How it works

1. Song structure JSON is loaded to determine section boundaries.
2. `timeline.py` maps sections to time-based segments with `(opacity, blend)`.
3. `composite.py` builds an ffmpeg `filter_complex` that:
   - Scales base and all overlays to `render.width × render.height`
   - Adjusts each overlay's duration via loop or last-frame hold
   - For each overlay, splits the stream per segment, applies per-segment blend, then concatenates
   - Chains multiple overlays sequentially (overlay 0 onto base, overlay 1 onto that result, etc.)
4. Output is encoded as H.264 / yuv420p.

### Example filter_complex (1 overlay, 2 sections)

```
[0:v]fps=24,scale=512:512:flags=lanczos[base_scaled];
[1:v]scale=512:512:flags=lanczos[ovl0_scaled];
[ovl0_scaled]loop=loop=-1:size=99999:start=0,trim=duration=10.000000,setpts=PTS-STARTPTS[ovl0];
[base_scaled]split=2[b0_0][b0_1];
[ovl0]split=2[o0_0][o0_1];
[b0_0]trim=start=0.000000:end=5.000000,setpts=PTS-STARTPTS[bs0_0];
[o0_0]trim=start=0.000000:end=5.000000,setpts=PTS-STARTPTS[os0_0];
[os0_0][bs0_0]blend=all_expr='A*0.5+B*0.5'[seg0_0];
[b0_1]trim=start=5.000000:end=10.000000,setpts=PTS-STARTPTS[bs0_1];
[o0_1]trim=start=5.000000:end=10.000000,setpts=PTS-STARTPTS[os0_1];
[os0_1][bs0_1]blend=all_expr='(255-(255-A)*(255-B)/255)*0.9+B*0.1'[seg0_1];
[seg0_0][seg0_1]concat=n=2:v=1:a=0[result0]
```

---

## Troubleshooting

**`Song structure JSON not found`** — Run `loom analyze --song path/to/song.mp3` first.

**`Base video not found`** — Check `paths.base` in your TOML config.

**ffmpeg fails with "Invalid option"** — Ensure ffmpeg is installed and on `$PATH` with libx264 support (`ffmpeg -codecs | grep libx264`).

**Composite looks wrong at section boundaries** — Verify section labels in `{song}.structure.json` match the `section` values in your `[[timeline]]` blocks exactly (case-sensitive).
