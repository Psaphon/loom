# Project Brief: loom

**Created:** 2026-04-12

## Pitch

Overnight pipeline that weaves footage, overlays, and audio into finished music videos.

## Problem / Motivation

Consumer AI music-video tools either generate from scratch (Neural Frames, BeatViz) or apply one-size effects (Kaiber), and none expose an API on free tiers for unattended batch work. Manually driving ComfyUI each render is slow and doesn't scale past one-off experiments. Loom turns ComfyUI + compositing into a config-driven pipeline: drop base footage, overlays, and a song into an input directory; wake up to a rendered music video; iterate by editing config, not by clicking.

## Target User

Self-hosted single-user tool. Operated from SSH (often phone via Tailscale) to queue jobs; output reviewed locally.

## Stack Preferences

- Language: Python 3.11+
- CLI: Click
- Config: TOML
- HTTP: httpx (async) for ComfyUI API calls
- Audio analysis: librosa
- Video compositing: ffmpeg (subprocess) for filter graphs and muxing
- Logging: Python `logging` module
- Paths: `pathlib.Path`
- External services: ComfyUI (via HTTP API, managed externally for now)
- Scheduling: systemd user timer, launched via dtl
- Hosting: local only

## Prior Art Considered

- **comfy-batcher** (rbbrdckybk) — CLI batch prompt sender for ComfyUI. Similar pattern to our ComfyUI client layer; worth reading before implementing ours, possibly borrowing prompt-queueing logic.
- **yvann-ba/ComfyUI_Yvann-Nodes** — audio-reactive ComfyUI nodes. Our audio-reactive feature will likely depend on these.
- **ComfyUI-Distributed** (robertvoy) — multi-GPU workflow distribution. Not needed now (single GPU), noted for future scale.
- **Standalone ComfyUI execution** (Quasilinear Musings) — running workflows without the ComfyUI server. Rejected for loom: we want ComfyUI running as a service, not embedded.
- **jeffwurfel-ttf/comfyui-pipeline** — Dockerized ComfyUI with API wrapper. Relevant to future `dtl service` primitive for managing ComfyUI; not loom's concern directly.

None of these solve loom's specific problem — song-structure-aware overlay compositing across base footage, overlays, and audio, orchestrated as a batch pipeline. That's the novel assembly.

## Must-Haves (v1)

- Accepts a project directory: base footage, overlay clips, song file, TOML config
- Analyzes song structure once, caches result as hand-editable JSON
- Preprocesses overlays via ComfyUI (lineart, canny, depth, hed extractors) into abstracted form
- Composites preprocessed overlays onto base footage with ffmpeg blend modes, modulated by song structure
- Optional diffusion stylization pass on base footage via AnimateDiff + ControlNet
- Produces final MP4 with synced audio
- Runs unattended via systemd timer in 00:00–05:30 window
- Resumable, idempotent, logs to file
- Does not collide with morning-brief (stops by 05:30)

## Nice-to-Haves (later)

- Draft mode skipping expensive passes for fast iteration
- Audio-reactive stem-level effect modulation (beyond section-level)
- Completion notifications (ntfy / email)
- Multi-project queue with priority
- Cloud-GPU fallback for renders exceeding 6GB VRAM

## Non-Goals

- Not real-time or interactive
- No GUI — CLI and config only
- Not cloud-hosted
- Not a general video editor (no cuts, trims, color grading beyond pipeline needs)
- Not replacing Neural Frames for polished one-off work; loom is for batch and iteration

## Risks & Unknowns

- **6GB VRAM ceiling.** AnimateDiff + ControlNet may OOM at useful resolutions. Mitigation: render at 512px base, upscale separately via Real-ESRGAN; rely on ComfyUI's smart offloading (documented to work down to 1GB VRAM with speed cost); disable diffusion feature via config if OOM proves unworkable.
- **Ollama/ComfyUI VRAM contention.** Both need the GPU. Mitigation: dtl-level time-boxed GPU windows (see dtl feature request). Loom owns GPU 00:00–05:30, morning-brief owns it after. Morning-brief's current 04:15 schedule moves to 05:30.
- **ComfyUI API stability across updates.** Workflow JSONs occasionally break. Mitigation: pin ComfyUI version, document it in `docs/comfyui-setup.md`.
- **Subjective quality.** v1 acceptance partly aesthetic. Technical criteria are testable; aesthetic review is a separate human gate post-merge.
- **Loop-break on failure.** AI developer may get stuck retrying broken renders. Mitigation: acceptance criteria must be unambiguously checkable; see dtl feature request for better loop-break semantics.

## Audience and Tone

- Target reader: a self-hosted operator running loom on their own GPU
- Tone: technical, terse, assumes familiarity with ComfyUI and ~/Projects conventions
- Pairs with: morning-brief (shares GPU, coordinates schedule), devtools (scaffolds and schedules loom), usb-autoinstall (may eventually install ComfyUI)

## Notes

- Hardware constraint: NVIDIA RTX 2060, 6GB VRAM, 32GB system RAM. Shapes every model choice.
- Morning-brief's schedule must move from 04:15 to 05:30 as part of loom's rollout. Not a loom feature; a coordination task the PM should flag.
- v1 includes diffusion. If it OOMs in practice, it becomes a configurable flag rather than getting removed.
- The "abstracted overlay" effect: preprocessor-only path (lineart/edges) is cheap and works; diffusion stylization of base footage is the expensive but high-impact path. Both are in v1.
