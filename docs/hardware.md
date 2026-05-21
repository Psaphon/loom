# Hardware Constraints

Loom is designed around a single GPU workstation with an NVIDIA RTX 2060 (6 GB VRAM). Every model choice, render resolution, and pipeline stage ordering follows from that constraint.

---

## Why 6 GB matters

Modern diffusion models are large. A full SD 1.5 checkpoint is ~4 GB on disk; loaded into VRAM with activations, KV cache, and ControlNet conditioning it pushes against 6 GB even at low resolutions.

Loom stays within budget by:

1. **Rendering at 512 × 512.** AnimateDiff + ControlNet runs at 512 px. This fits in 6 GB with ComfyUI's default memory management.
2. **Upscaling separately.** Real-ESRGAN upscales to 1080p as a second pass. The upscaler is a much lighter model and processes frames individually.
3. **Not loading models simultaneously.** Each pipeline stage (preprocess, stylize, upscale) is a separate ComfyUI job. ComfyUI unloads models between jobs when memory pressure is high.
4. **Making diffusion optional.** `enable_diffusion = false` skips AnimateDiff entirely. Compositing from preprocessed overlays (lineart, canny) uses no VRAM beyond what ffmpeg needs (zero GPU).

---

## VRAM budget by stage

| Stage | VRAM use | Notes |
|-------|----------|-------|
| Analyze | 0 | CPU + RAM only (librosa) |
| Preprocess (canny) | ~1 GB | CannyEdgePreprocessor — purely algorithmic |
| Preprocess (lineart/hed) | ~2 GB | Learned preprocessors load small models |
| Preprocess (depth) | ~2 GB | MiDaS model |
| Stylize (AnimateDiff + ControlNet) | ~5–6 GB | Tight at 512 px; may OOM above 512 px |
| Composite | 0 | CPU only (ffmpeg) |
| Upscale (Real-ESRGAN) | ~2 GB | Processes per frame; does not need full video in VRAM |
| Mux | 0 | CPU only (ffmpeg) |

---

## When OOM happens

AnimateDiff is the stage most likely to OOM. ComfyUI has three memory modes:

| Flag | VRAM use | Speed |
|------|----------|-------|
| (default) | Most VRAM | Fastest |
| `--lowvram` | Less VRAM (offloads to RAM) | Slower |
| `--novram` | Minimum VRAM (offloads fully) | Slowest |

If loom's stylize stage fails with `OOMError`:

1. Restart ComfyUI with `--lowvram`.
2. If still OOM, add `--novram`. Inference will be slow but should complete.
3. If neither works, set `enable_diffusion = false`. The output loses the AnimateDiff stylization but compositing still runs.

OOM is caught and reported cleanly — the pipeline exits with code 1 rather than hanging or looping.

---

## Model choices

All models are SD 1.5-based specifically to fit the 6 GB budget:

| Model | Why SD 1.5 |
|-------|-----------|
| AnimateDiff motion module (`mm_sd_v15_v2.ckpt`) | SD 1.5 motion modules are well-supported and smaller than SDXL equivalents |
| ControlNet (`control_v11*_sd15_*.pth`) | SD 1.5 ControlNet fits alongside the base model in 6 GB |
| Real-ESRGAN (`RealESRGAN_x4plus.pth`) | Upscaler — lightweight, not a diffusion model |

Newer model families (SDXL, SD 3, Flux) require significantly more VRAM and are not supported.

---

## System RAM

librosa loads the full audio file into RAM for analysis. ffmpeg buffers frames during composite. 16 GB system RAM is the comfortable minimum; 8 GB may work for short clips but can cause swapping during composite on longer videos.

---

## Storage

Intermediate renders accumulate in `output/`:

| File | Approximate size (10-min video at 512 px) |
|------|-------------------------------------------|
| `composite.mp4` | 200–500 MB |
| `upscaled.mp4` | 1–3 GB |
| `final.mp4` | 500 MB–1.5 GB |

Total: allow 5–10 GB free per project. Clean intermediate files after a successful render if space is tight.

---

## Multi-GPU / cloud GPU

Not supported in v1. Loom assumes one local GPU. Cloud GPU fallback and multi-GPU distribution are noted as future work in `docs/BRIEF.md`.
