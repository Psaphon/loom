"""Tests for upscale.py using mocked ComfyUI and ffmpeg."""

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
from loom.upscale import (
    OOMError,
    UpscaleError,
    _build_workflow,
    _check_oom,
    _choose_model,
    upscale_video,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = "http://127.0.0.1:8188"
PROMPT_ID = "upscale-job-aaaa-bbbb-cccc"
FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

HISTORY_OK = {
    PROMPT_ID: {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {
            "4": {
                "images": [
                    {
                        "filename": "loom_upscale_00001_.png",
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
                    {"exception_message": "CUDA out of memory. Tried to allocate 512.00 MiB."},
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
            "messages": [["execution_error", {"exception_message": "model not found"}]],
        },
        "outputs": {},
    }
}

MINIMAL_TEMPLATE = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "loom_input_image", "upload": "image"},
    },
    "2": {
        "class_type": "UpscaleModelLoader",
        "inputs": {"model_name": "loom_model_name"},
    },
    "3": {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"images": ["3", 0], "filename_prefix": "loom_upscale"},
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_dir(tmp_path: Path) -> Path:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "real_esrgan.json").write_text(json.dumps(MINIMAL_TEMPLATE))
    return wf_dir


@pytest.fixture
def fake_input_video(tmp_path: Path) -> Path:
    p = tmp_path / "low.mp4"
    p.write_bytes(b"fake video bytes")
    return p


def _make_subprocess_mock(tmp_path: Path, frame_count: int = 2, fps: str = "24/1"):
    """Return a fake subprocess.run that simulates ffprobe and ffmpeg."""

    def fake_run(cmd, capture_output=False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""
        cmd_str = " ".join(str(c) for c in cmd)

        if "ffprobe" in cmd[0]:
            # Resolution probe returns "512,512"; fps probe returns fps string.
            if "width,height" in cmd_str:
                result.stdout = b"512,512"
            else:
                result.stdout = fps.encode()
            return result

        if "ffmpeg" in cmd[0] and "frame_%06d.png" in cmd_str:
            # Determine if this is extraction (output pattern) or encoding (input pattern).
            # Extract: ffmpeg ... -i video -o dir/frame_%06d.png
            # Encode:  ffmpeg ... -i dir/frame_%06d.png ... output.mp4
            input_idx = cmd.index("-i") if "-i" in cmd else -1
            if input_idx != -1 and "frame_%06d.png" in str(cmd[input_idx + 1]):
                # Encoding pass — just succeed.
                result.stdout = b""
                return result
            # Extraction pass — write fake frames.
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
# _choose_model unit tests
# ---------------------------------------------------------------------------


def test_choose_model_uses_4x_for_1080p_from_512() -> None:
    # 512 → 1920: scale ≈ 3.75 → 4×
    assert _choose_model(512, 512, 1920, 1080) == "RealESRGAN_x4plus.pth"


def test_choose_model_uses_4x_for_4k_from_512() -> None:
    assert _choose_model(512, 512, 3840, 2160) == "RealESRGAN_x4plus.pth"


def test_choose_model_uses_2x_when_scale_leq_2() -> None:
    # 960 → 1920: scale = 2.0 → 2×
    assert _choose_model(960, 540, 1920, 1080) == "RealESRGAN_x2plus.pth"


def test_choose_model_uses_4x_when_scale_just_above_2() -> None:
    assert _choose_model(900, 506, 1920, 1080) == "RealESRGAN_x4plus.pth"


# ---------------------------------------------------------------------------
# _build_workflow unit tests
# ---------------------------------------------------------------------------


def test_build_workflow_injects_filename() -> None:
    wf = _build_workflow(MINIMAL_TEMPLATE, "frame_000001.png", "RealESRGAN_x4plus.pth")
    load_node = next(n for n in wf.values() if n.get("class_type") == "LoadImage")
    assert load_node["inputs"]["image"] == "frame_000001.png"


def test_build_workflow_injects_model_name() -> None:
    wf = _build_workflow(MINIMAL_TEMPLATE, "frame.png", "RealESRGAN_x2plus.pth")
    model_node = next(n for n in wf.values() if n.get("class_type") == "UpscaleModelLoader")
    assert model_node["inputs"]["model_name"] == "RealESRGAN_x2plus.pth"


def test_build_workflow_missing_stub_raises() -> None:
    bad_template = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "something_else.png"}},
    }
    with pytest.raises(UpscaleError, match="loom_input_image"):
        _build_workflow(bad_template, "frame.png", "RealESRGAN_x4plus.pth")


def test_build_workflow_does_not_mutate_template() -> None:
    import copy

    original = copy.deepcopy(MINIMAL_TEMPLATE)
    _build_workflow(MINIMAL_TEMPLATE, "frame.png", "RealESRGAN_x4plus.pth")
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
    exc = ComfyJobError("job-xyz", "model not found: RealESRGAN_x4plus.pth")
    # Should not raise
    _check_oom(exc)


# ---------------------------------------------------------------------------
# upscale_video — missing workflow
# ---------------------------------------------------------------------------


def test_upscale_video_missing_workflow_raises(tmp_path: Path, fake_input_video: Path) -> None:
    empty_dir = tmp_path / "empty_wf"
    empty_dir.mkdir()
    with pytest.raises(UpscaleError, match="Workflow file not found"):
        asyncio.run(
            upscale_video(
                fake_input_video,
                tmp_path / "out.mp4",
                workflows_dir=empty_dir,
            )
        )


def test_upscale_video_unknown_target_raises(tmp_path: Path, fake_input_video: Path) -> None:
    with pytest.raises(ValueError, match="Unknown target resolution"):
        asyncio.run(
            upscale_video(
                fake_input_video,
                tmp_path / "out.mp4",
                target="720p",
            )
        )


# ---------------------------------------------------------------------------
# upscale_video — ComfyUI down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_comfy_down(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    respx.post(f"{BASE}/upload/image").mock(side_effect=httpx.ConnectError("refused"))

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        with pytest.raises(UpscaleError, match="ComfyUI error"):
            await upscale_video(
                fake_input_video,
                tmp_path / "out.mp4",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# upscale_video — OOM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_oom(
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
            await upscale_video(
                fake_input_video,
                tmp_path / "out.mp4",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# upscale_video — generic job error (non-OOM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_job_error(
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
        with pytest.raises(UpscaleError, match="ComfyUI error"):
            await upscale_video(
                fake_input_video,
                tmp_path / "out.mp4",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# upscale_video — happy path (single frame)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_happy_path_single_frame(
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

    output = tmp_path / "high.mp4"

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        await upscale_video(
            fake_input_video,
            output,
            "1080p",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )

    assert output.parent.exists()


# ---------------------------------------------------------------------------
# upscale_video — happy path (multi-frame)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_multi_frame(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    """Three frames → three separate ComfyUI job submissions."""
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
        await upscale_video(
            fake_input_video,
            tmp_path / "out.mp4",
            "1080p",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )

    # One prompt submission per frame.
    prompt_calls = [c for c in respx.calls if c.request.url.path == "/prompt"]
    assert len(prompt_calls) == frame_count


# ---------------------------------------------------------------------------
# upscale_video — 4k target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upscale_video_4k_target(
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

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        await upscale_video(
            fake_input_video,
            tmp_path / "out_4k.mp4",
            "4k",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )
