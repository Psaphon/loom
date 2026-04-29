"""Tests for stylize.py using mocked ComfyUI and ffmpeg."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from loom.comfy import ComfyJobError
from loom.stylize import (
    OOMError,
    StylizeError,
    _build_workflow,
    _check_oom,
    stylize_video,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = "http://127.0.0.1:8188"
PROMPT_ID = "stylize-job-aaaa-bbbb-cccc"
FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

HISTORY_OK = {
    PROMPT_ID: {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {
            "12": {
                "images": [
                    {
                        "filename": "loom_stylize_00001_.png",
                        "subfolder": "",
                        "type": "output",
                    },
                ]
            }
        },
    }
}

HISTORY_OOM = {
    PROMPT_ID: {
        "status": {
            "status_str": "error",
            "completed": True,
            "messages": [
                [
                    "execution_error",
                    {"exception_message": ("CUDA out of memory. Tried to allocate 512.00 MiB.")},
                ]
            ],
        },
        "outputs": {},
    }
}

HISTORY_ERROR = {
    PROMPT_ID: {
        "status": {
            "status_str": "error",
            "completed": True,
            "messages": [["execution_error", {"exception_message": "node not found"}]],
        },
        "outputs": {},
    }
}

# Minimal template matching the real workflow structure.
MINIMAL_TEMPLATE = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "loom_batch_input", "upload": "image"},
    },
    "2": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"},
    },
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "loom_positive_prompt", "clip": ["2", 1]},
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "loom_negative_prompt", "clip": ["2", 1]},
    },
    "5": {
        "class_type": "LineArtPreprocessor",
        "inputs": {"image": ["1", 0], "resolution": 512, "coarse": "disable"},
    },
    "6": {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": "control_v11p_sd15_lineart.pth"},
    },
    "7": {
        "class_type": "ControlNetApply",
        "inputs": {
            "conditioning": ["3", 0],
            "control_net": ["6", 0],
            "image": ["5", 0],
            "strength": 0.8,
        },
    },
    "8": {
        "class_type": "ADE_AnimateDiffLoaderGen1",
        "inputs": {
            "model_name": "mm_sd_v15_v2.ckpt",
            "beta_schedule": "sqrt_linear (AnimateDiff)",
            "model": ["2", 0],
        },
    },
    "9": {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["1", 0], "vae": ["2", 2]},
    },
    "10": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["8", 0],
            "positive": ["7", 0],
            "negative": ["4", 0],
            "latent_image": ["9", 0],
            "seed": 0,
            "steps": 1,
            "cfg": 7.0,
            "sampler_name": "euler_ancestral",
            "scheduler": "karras",
            "denoise": 0.75,
        },
    },
    "11": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["10", 0], "vae": ["2", 2]},
    },
    "12": {
        "class_type": "SaveImage",
        "inputs": {"images": ["11", 0], "filename_prefix": "loom_stylize"},
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_dir(tmp_path: Path) -> Path:
    """Temp directory with the AnimateDiff workflow JSON."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "animatediff_controlnet.json").write_text(json.dumps(MINIMAL_TEMPLATE))
    return wf_dir


@pytest.fixture
def fake_input_video(tmp_path: Path) -> Path:
    p = tmp_path / "base.mp4"
    p.write_bytes(b"fake video bytes")
    return p


def _make_subprocess_mock(tmp_path: Path, frame_count: int = 3, fps: str = "24/1"):
    def fake_run(cmd, capture_output=False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""
        cmd_str = " ".join(str(c) for c in cmd)
        if "ffprobe" in cmd[0]:
            result.stdout = fps.encode()
            return result
        if "ffmpeg" in cmd[0] and "frame_%06d.png" in cmd_str:
            out_pattern = next(c for c in cmd if "frame_%06d.png" in str(c))
            out_dir = Path(str(out_pattern)).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            for i in range(frame_count):
                (out_dir / f"frame_{i + 1:06d}.png").write_bytes(FAKE_PNG)
            result.stdout = b""
            return result
        result.stdout = b""
        return result

    return fake_run


# ---------------------------------------------------------------------------
# _build_workflow unit tests
# ---------------------------------------------------------------------------


def test_build_workflow_single_frame_replaces_stub() -> None:
    wf = _build_workflow(
        MINIMAL_TEMPLATE, ["frame_000001.png"], "watercolor", "bad", 512, 512, 20, 7.0, 42
    )
    # Stub node "1" should still exist with updated filename
    assert wf["1"]["inputs"]["image"] == "frame_000001.png"


def test_build_workflow_single_frame_injects_prompts() -> None:
    wf = _build_workflow(
        MINIMAL_TEMPLATE, ["frame.png"], "oil painting", "blurry", 512, 512, 20, 7.0, 1
    )
    texts = {
        node["inputs"]["text"] for node in wf.values() if node.get("class_type") == "CLIPTextEncode"
    }
    assert "oil painting" in texts
    assert "blurry" in texts


def test_build_workflow_single_frame_injects_ksampler_params() -> None:
    wf = _build_workflow(MINIMAL_TEMPLATE, ["frame.png"], "x", "y", 512, 512, 30, 8.5, 99)
    ksampler = next(n for n in wf.values() if n.get("class_type") == "KSampler")
    assert ksampler["inputs"]["steps"] == 30
    assert ksampler["inputs"]["cfg"] == pytest.approx(8.5)
    assert ksampler["inputs"]["seed"] == 99


def test_build_workflow_single_frame_injects_resolution() -> None:
    wf = _build_workflow(MINIMAL_TEMPLATE, ["frame.png"], "x", "y", 384, 384, 20, 7.0, 0)
    preprocessor = next(n for n in wf.values() if n.get("class_type") == "LineArtPreprocessor")
    assert preprocessor["inputs"]["resolution"] == 384


def test_build_workflow_multi_frame_creates_batch_nodes() -> None:
    wf = _build_workflow(
        MINIMAL_TEMPLATE,
        ["f1.png", "f2.png", "f3.png"],
        "sketch",
        "bad",
        512,
        512,
        20,
        7.0,
        0,
    )
    load_nodes = [n for n in wf.values() if n.get("class_type") == "LoadImage"]
    batch_nodes = [n for n in wf.values() if n.get("class_type") == "ImageBatch"]
    # 3 frames → 3 LoadImage + 2 ImageBatch
    assert len(load_nodes) == 3
    assert len(batch_nodes) == 2


def test_build_workflow_multi_frame_removes_stub() -> None:
    wf = _build_workflow(
        MINIMAL_TEMPLATE,
        ["f1.png", "f2.png"],
        "x",
        "y",
        512,
        512,
        20,
        7.0,
        0,
    )
    # Original stub node "1" should be gone (replaced by high-ID nodes)
    assert "1" not in wf


def test_build_workflow_multi_frame_rewires_references() -> None:
    """All nodes that referenced stub node '1' should be rewired to ImageBatch output."""
    wf = _build_workflow(
        MINIMAL_TEMPLATE,
        ["f1.png", "f2.png"],
        "x",
        "y",
        512,
        512,
        20,
        7.0,
        0,
    )
    # No node should reference the old stub ID "1" anymore.
    for node in wf.values():
        for val in node.get("inputs", {}).values():
            if isinstance(val, list) and len(val) == 2:
                assert val[0] != "1", f"Stale reference to stub node '1' in: {node}"


def test_build_workflow_missing_stub_raises() -> None:
    bad_template = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "something_else.png"}},
    }
    with pytest.raises(StylizeError, match="loom_batch_input"):
        _build_workflow(bad_template, ["frame.png"], "x", "y", 512, 512, 20, 7.0, 0)


def test_build_workflow_does_not_mutate_template() -> None:
    import copy

    original = copy.deepcopy(MINIMAL_TEMPLATE)
    _build_workflow(MINIMAL_TEMPLATE, ["frame.png"], "style", "neg", 512, 512, 20, 7.0, 0)
    assert MINIMAL_TEMPLATE == original


# ---------------------------------------------------------------------------
# _check_oom tests
# ---------------------------------------------------------------------------


def test_check_oom_raises_on_cuda_message() -> None:
    exc = ComfyJobError("job-xyz", "CUDA out of memory. Tried to allocate 512 MiB")
    with pytest.raises(OOMError, match="out of GPU memory"):
        _check_oom(exc)


def test_check_oom_raises_on_out_of_memory_lower() -> None:
    exc = ComfyJobError("job-xyz", "RuntimeError: out of memory")
    with pytest.raises(OOMError):
        _check_oom(exc)


def test_check_oom_silent_on_other_errors() -> None:
    exc = ComfyJobError("job-xyz", "node not found: LineArtPreprocessor")
    # Should not raise
    _check_oom(exc)


# ---------------------------------------------------------------------------
# stylize_video — missing workflow
# ---------------------------------------------------------------------------


def test_stylize_video_missing_workflow_raises(tmp_path: Path, fake_input_video: Path) -> None:
    empty_dir = tmp_path / "empty_wf"
    empty_dir.mkdir()
    with pytest.raises(StylizeError, match="Workflow file not found"):
        asyncio.run(
            stylize_video(
                fake_input_video,
                tmp_path / "out.mp4",
                "watercolor",
                workflows_dir=empty_dir,
            )
        )


# ---------------------------------------------------------------------------
# stylize_video — ComfyUI down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_comfy_down(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    respx.post(f"{BASE}/upload/image").mock(side_effect=httpx.ConnectError("refused"))

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        with pytest.raises(StylizeError, match="ComfyUI error"):
            await stylize_video(
                fake_input_video,
                tmp_path / "out.mp4",
                "watercolor",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# stylize_video — OOM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_oom(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    upload_resp = {"name": "frame_000001.png", "subfolder": "", "type": "input"}
    respx.post(f"{BASE}/upload/image").mock(return_value=httpx.Response(200, json=upload_resp))
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_OOM)
    )

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        with pytest.raises(OOMError, match="out of GPU memory"):
            await stylize_video(
                fake_input_video,
                tmp_path / "out.mp4",
                "watercolor",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# stylize_video — generic job error (non-OOM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_job_error(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    upload_resp = {"name": "frame_000001.png", "subfolder": "", "type": "input"}
    respx.post(f"{BASE}/upload/image").mock(return_value=httpx.Response(200, json=upload_resp))
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_ERROR)
    )

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        with pytest.raises(StylizeError, match="ComfyUI error"):
            await stylize_video(
                fake_input_video,
                tmp_path / "out.mp4",
                "watercolor",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# stylize_video — happy path (single frame)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_happy_path_single_frame(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(
            200, json={"name": "frame_000001.png", "subfolder": "", "type": "input"}
        )
    )
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json=HISTORY_OK))
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=FAKE_PNG))

    output = tmp_path / "stylized.mp4"

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        await stylize_video(
            fake_input_video,
            output,
            "watercolor painting",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )

    assert output.parent.exists()


# ---------------------------------------------------------------------------
# stylize_video — multi-frame batch (context_length=16, 3 frames)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_multi_frame_single_batch(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    """Three frames fit in one context window — one workflow submission expected."""
    frame_count = 3

    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "f.png", "subfolder": "", "type": "input"})
    )
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json=HISTORY_OK))
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=FAKE_PNG))

    with patch(
        "subprocess.run",
        side_effect=_make_subprocess_mock(tmp_path, frame_count=frame_count),
    ):
        await stylize_video(
            fake_input_video,
            tmp_path / "out.mp4",
            "oil painting",
            comfy_url=BASE,
            context_length=16,
            workflows_dir=workflow_dir,
        )

    # 3 uploads + 1 prompt + 1 history(wait) + 1 history(fetch) + 1 view = 7 calls
    assert respx.calls.call_count == frame_count + 1 + 1 + 1 + 1


# ---------------------------------------------------------------------------
# stylize_video — multiple context windows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stylize_video_multiple_context_windows(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    """5 frames with context_length=3 → 2 batches (3 + 2)."""
    frame_count = 5

    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "f.png", "subfolder": "", "type": "input"})
    )
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json=HISTORY_OK))
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=FAKE_PNG))

    with patch(
        "subprocess.run",
        side_effect=_make_subprocess_mock(tmp_path, frame_count=frame_count),
    ):
        await stylize_video(
            fake_input_video,
            tmp_path / "out.mp4",
            "sketch",
            comfy_url=BASE,
            context_length=3,
            workflows_dir=workflow_dir,
        )

    # 2 batches → 2 prompt submissions, 2 wait polls, 2 fetch history, 2 view downloads
    prompt_calls = [c for c in respx.calls if c.request.url.path == "/prompt"]
    assert len(prompt_calls) == 2
