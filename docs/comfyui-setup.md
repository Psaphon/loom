# ComfyUI Setup for Loom

Loom does not install or manage ComfyUI. It assumes a running instance is
reachable at `http://127.0.0.1:8188` (configurable via the project TOML).

---

## Requirements

| Component | Tested version |
|-----------|---------------|
| ComfyUI | latest `main` |
| Python | 3.11+ (ComfyUI's own env) |
| GPU | NVIDIA RTX 2060 6 GB or better |
| CUDA | 11.8 or 12.x |

---

## Installation (one-time)

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI
python -m venv venv
source venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
pip install -r requirements.txt
```

---

## Required custom nodes

Install via ComfyUI Manager or manually into `ComfyUI/custom_nodes/`:

| Node pack | Purpose |
|-----------|---------|
| `ComfyUI-AnimateDiff-Evolved` | AnimateDiff motion module support |
| `comfyui_controlnet_aux` | ControlNet preprocessors (lineart, canny, depth, HED) |
| `ComfyUI-VideoHelperSuite` | Video I/O nodes |

---

## Required model checkpoints

Place under `ComfyUI/models/`:

| Path | Model |
|------|-------|
| `checkpoints/v1-5-pruned-emaonly.ckpt` | Stable Diffusion 1.5 base |
| `animatediff_models/mm_sd_v15_v2.ckpt` | AnimateDiff motion module v2 |
| `upscale_models/RealESRGAN_x4plus.pth` | Real-ESRGAN ×4 upscaler |
| `controlnet/control_v11p_sd15_lineart.pth` | Lineart ControlNet |
| `controlnet/control_v11p_sd15_canny.pth` | Canny ControlNet |
| `controlnet/control_v11f1p_sd15_depth.pth` | Depth ControlNet |
| `controlnet/control_v11p_sd15_softedge.pth` | HED / SoftEdge ControlNet |

All SD 1.5-based — sized to fit the 6 GB VRAM budget.

---

## Starting ComfyUI

```bash
cd ~/ComfyUI
source venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

Loom expects ComfyUI to be listening before any pipeline stage runs.
The systemd service unit (`loom.service`) should have a dependency on a
ComfyUI service or `ExecStartPre` health-check if ComfyUI is managed by
systemd as well.

### Quick health check

```bash
curl -s http://127.0.0.1:8188/system_stats | python -m json.tool
```

A 200 response means ComfyUI is ready.

---

## Loom configuration

In your project TOML:

```toml
[comfyui]
base_url = "http://127.0.0.1:8188"
timeout  = 3600          # per-job timeout in seconds
```

If `[comfyui]` is omitted, defaults are used (`127.0.0.1:8188`, 3600 s).

---

## GPU memory notes

- Diffusion stages run at 512 px to stay within 6 GB.
- `enable_diffusion = false` skips AnimateDiff/ControlNet entirely (fast path).
- Real-ESRGAN upscale runs as a separate ComfyUI job after compositing.
- Do not load multiple large models simultaneously — ComfyUI loads on demand.
