# Troubleshooting

Common failures and how to fix them. For scheduling-specific issues see [`docs/scheduling.md`](scheduling.md).

---

## ComfyUI not reachable

**Symptom:** `ConnectionRefusedError` or `ComfyUI not reachable at http://127.0.0.1:8188`

**Cause:** ComfyUI is not running, or is listening on a different address/port.

**Fix:**
1. Start ComfyUI: `cd ~/ComfyUI && source venv/bin/activate && python main.py --listen 127.0.0.1 --port 8188`
2. Verify: `curl -s http://127.0.0.1:8188/system_stats | python -m json.tool`
3. If using a non-default address, set it in `loom.toml`:
   ```toml
   [comfyui]
   base_url = "http://127.0.0.1:8188"
   ```

Loom does not start or manage ComfyUI. It must be running before any pipeline stage that calls it (preprocess, stylize, upscale).

---

## GPU out of memory (OOM) during diffusion

**Symptom:** Pipeline exits with `OOMError` or ComfyUI returns an OOM response during the stylize stage.

**Cause:** AnimateDiff + ControlNet exceeds available VRAM. On 6 GB this can happen at resolutions above 512 px or with longer context windows.

**Fix (in order of preference):**
1. Ensure `render.width` and `render.height` are both `512` (the default).
2. Start ComfyUI with `--lowvram` or `--novram` flags to enable aggressive model offloading (slower but fits in less VRAM).
3. Disable diffusion entirely: set `enable_diffusion = false` in `[render]`. Compositing still works; you skip the AnimateDiff stylization pass.

OOM is non-retriable — the pipeline exits immediately rather than looping. Fix the VRAM issue and re-run; completed stages are skipped.

See [`docs/hardware.md`](hardware.md) for a full explanation of VRAM constraints.

---

## Song structure JSON not found

**Symptom:** `FileNotFoundError` for `{song_name}.structure.json` during composite stage.

**Fix:**
```bash
loom analyze --song input/song.mp3
```

The JSON is written beside the song file and cached. Downstream stages read it; librosa is not re-run unless the song file changes or `--force` is passed.

---

## Section labels don't match timeline config

**Symptom:** Overlay opacity is uniform across the video; `[[timeline]]` rules appear to have no effect.

**Cause:** Section labels in `song.structure.json` (e.g. `"section_0"`) do not match the `section` values in `[[timeline]]` blocks.

**Fix:** Edit `song.structure.json` and rename the labels to match your config:
```json
{ "start": 0.0, "end": 32.1, "label": "intro" }
```
Labels are case-sensitive. Sections not matched by any rule use `opacity = 1.0`, `blend = "normal"`.

See [`docs/song-structure.md`](song-structure.md) for the full schema and hand-editing guide.

---

## ffmpeg not found or missing libx264

**Symptom:** `FileNotFoundError: ffmpeg` or `Unknown encoder 'libx264'`

**Fix:**
```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# Verify libx264 support
ffmpeg -codecs 2>/dev/null | grep libx264
```

ffmpeg must be on `$PATH` with H.264 encoding support. The system package is sufficient.

---

## Preprocess produces blank output

**Symptom:** The preprocessed overlay clip is all black or all white.

**Cause:** Usually a mismatch between the workflow JSON parameters and the input clip's properties (resolution, bit depth).

**Fix:**
1. Check that the workflow JSON in `workflows/preprocess/{style}.json` has `resolution` matching your `render.width`.
2. Ensure ComfyUI loaded the required custom node (e.g. `comfyui_controlnet_aux` for lineart/canny/hed/depth). Check ComfyUI's console output on startup.
3. Try a different style (`canny` has no learned component and is the most reliable):
   ```bash
   loom preprocess --input input/overlays/fx.mp4 --style canny --output input/overlays/fx.canny.mp4
   ```

---

## Pipeline stops mid-render (deadline reached)

**Symptom:** Pipeline exits with code `2` before `output/final.mp4` is produced.

**Cause:** The deadline (default `05:30`) was reached between stages. This is expected behaviour.

**Fix:** Re-run the next night. Completed stages are recorded in `output/.loom-state.json` and skipped automatically:
```bash
loom run --config loom.toml
```

To adjust the deadline:
```toml
[schedule]
deadline = "04:00"
```
Or set `LOOM_DEADLINE=04:00` in the environment.

---

## Composite looks wrong at section boundaries

**Symptom:** Visible cuts or incorrect blend at the point where sections change.

**Cause:** Section boundaries in the structure JSON leave gaps, overlaps, or are at non-frame-aligned timestamps.

**Fix:** Edit `song.structure.json` so sections are contiguous (no gap between `end` of one and `start` of next). Timestamps are in seconds; ffmpeg trims to frame boundaries automatically. Re-run composite:
```bash
loom composite --config loom.toml
```
Because composite is a later stage, you can re-run `loom run` and only composite + upscale + mux will re-execute (analyze and preprocess are cached).

---

## Output video has no audio

**Symptom:** `output/final.mp4` plays video but is silent.

**Cause:** `paths.song` is wrong or missing in `loom.toml`.

**Fix:** Verify the path:
```toml
[paths]
song = "input/song.mp3"
```
Then re-run with `--force` to redo the mux stage:
```bash
loom run --config loom.toml --force
```

---

## Upscale produces wrong resolution

**Symptom:** `output/upscaled.mp4` is not 1080p, or aspect ratio is distorted.

**Cause:** Real-ESRGAN applies a fixed scale factor (×2 or ×4). At 512 px input, ×2 gives 1024 px and ×4 gives 2048 px — neither is exactly 1080.

**Fix:** The upscale stage targets 1080p by choosing the appropriate scale factor and applying a final ffmpeg resize if needed. If the output resolution is wrong, check `render.width` and `render.height` in the config. Both should be `512` for the RTX 2060 workflow.

---

## Log location

All pipeline output is written to `output/loom.log` (appended on each run) and to stdout. Run with `--debug` for verbose output:

```bash
loom run --config loom.toml --debug
```

For systemd-managed runs:
```bash
journalctl --user -u loom.service -n 100
```
