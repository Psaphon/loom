# Preprocess Styles

`loom preprocess` sends each frame of an overlay clip through a ComfyUI
preprocessor workflow and re-encodes the result.  Four styles are supported.
All are cheap deterministic operations — no diffusion, no model sampling.

---

## lineart

**Node:** `LineArtPreprocessor` (ComfyUI ControlNet Auxiliary Preprocessors)

Produces clean, inked line art from the source frame.  Best for:

- Cartoon or anime overlay clips where you want isolated contours
- Clips with clear object boundaries and little texture detail
- Situations where you want the overlay to read as a drawn element over the base footage

Coarse mode is disabled by default; enable it in the workflow JSON for a rougher,
sketchier result.

---

## canny

**Node:** `CannyEdgePreprocessor`

Canny edge detection.  Produces sharp, precise edge maps as white lines on black.
Best for:

- Technical or architectural overlays
- Clips with strong geometric structure
- When you want fine detail preserved (adjust `low_threshold` / `high_threshold`
  in the workflow JSON to control sensitivity)

Canny is faster than lineart and has no learned component.

---

## depth

**Node:** `MiDaS-DepthMapPreprocessor`

MiDaS monocular depth estimation.  Returns a grayscale depth map where near
is bright and far is dark.  Best for:

- Depth-of-field or fog compositing effects
- Parallax-style blend modes where depth controls opacity
- Clips with meaningful foreground/background separation

Requires the MiDaS model weights to be present in ComfyUI's model directory.
See `docs/comfyui-setup.md` for model installation.

---

## hed

**Node:** `HEDPreprocessor`

Holistically-Nested Edge Detection.  Softer, more organic edges than Canny —
the model is trained on natural image boundaries.  Best for:

- Natural textures and organic shapes (foliage, water, fabric)
- Overlays where hard Canny edges look too mechanical
- Creative edge effects where partial edges and soft transitions are desirable

`safe` mode is enabled by default; disable in the workflow JSON if you want
more aggressive edge extraction.

---

## Choosing a Style

| Style    | Speed  | Learned | Output character              |
|----------|--------|---------|-------------------------------|
| canny    | fast   | no      | Sharp, precise, geometric     |
| lineart  | medium | yes     | Clean inked lines, manga-like |
| hed      | medium | yes     | Soft, organic, partial edges  |
| depth    | medium | yes     | Grayscale depth map           |

Start with `canny` for a quick preview.  Switch to `lineart` or `hed` when the
output needs to feel more hand-drawn or organic.  Use `depth` only when the
compositing blend mode needs depth information.

---

## Workflow JSON Customization

The workflow files in `workflows/preprocess/` are ComfyUI API-format JSONs.
They are assets, not code — edit them freely to adjust node parameters without
touching Python.  Key parameters to tune:

- **`resolution`** (all styles): output resolution passed to the preprocessor.
  Match this to your render width (`render.width` in project TOML).
- **`low_threshold` / `high_threshold`** (canny): edge sensitivity.
- **`coarse`** (lineart): `"disable"` for fine lines, `"enable"` for rough sketch.
- **`safe`** (hed): `"enable"` prevents the model from hallucinating edges.
