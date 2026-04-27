"""Tests for preprocess.py and video_io.py using mocked ComfyUI and ffmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from loom.preprocess import (
    PreprocessError,
    _inject_input_image,
    preprocess_overlay,
)
from loom.video_io import VideoIOError, encode_frames, extract_frames, get_video_fps

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = "http://127.0.0.1:8188"
PROMPT_ID = "test-job-aaaa-bbbb-cccc"
FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # PNG magic + partial IHDR

HISTORY_OK = {
    PROMPT_ID: {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {
            "3": {
                "images": [
                    {"filename": "loom_lineart_00001_.png", "subfolder": "", "type": "output"},
                ]
            }
        },
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

MINIMAL_WORKFLOW = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "input.png", "upload": "image"},
    },
    "2": {
        "class_type": "LineArtPreprocessor",
        "inputs": {"image": ["1", 0], "resolution": 512},
    },
    "3": {
        "class_type": "SaveImage",
        "inputs": {"images": ["2", 0], "filename_prefix": "loom_lineart"},
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_dir(tmp_path: Path) -> Path:
    """Temp directory with all four workflow JSON files."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    for style in ("lineart", "canny", "depth", "hed"):
        (wf_dir / f"{style}.json").write_text(json.dumps(MINIMAL_WORKFLOW))
    return wf_dir


@pytest.fixture
def fake_input_video(tmp_path: Path) -> Path:
    """A placeholder video file (content irrelevant; ffmpeg is mocked)."""
    p = tmp_path / "overlay.mp4"
    p.write_bytes(b"fake video bytes")
    return p


def _make_subprocess_mock(tmp_path: Path, frame_count: int = 3, fps: str = "24/1"):
    """Return a mock for subprocess.run that fakes ffprobe/ffmpeg behaviour.

    When ffmpeg is asked to extract frames it creates ``frame_NNNNNN.png``
    files in the output directory so downstream code can find them.
    """

    def fake_run(cmd: list[str], capture_output: bool = False, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = b""

        cmd_str = " ".join(str(c) for c in cmd)

        if "ffprobe" in cmd[0]:
            result.stdout = fps.encode()
            return result

        # ffmpeg frame extraction: find the output pattern arg and create files.
        if "ffmpeg" in cmd[0] and "frame_%06d.png" in cmd_str:
            out_pattern = next(c for c in cmd if "frame_%06d.png" in str(c))
            out_dir = Path(str(out_pattern)).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            for i in range(frame_count):
                (out_dir / f"frame_{i + 1:06d}.png").write_bytes(FAKE_PNG)
            result.stdout = b""
            return result

        # ffmpeg encoding pass — just succeed silently.
        result.stdout = b""
        return result

    return fake_run


# ---------------------------------------------------------------------------
# video_io unit tests
# ---------------------------------------------------------------------------


def test_get_video_fps_integer(tmp_path: Path) -> None:
    fake = tmp_path / "v.mp4"
    fake.write_bytes(b"x")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"30/1", stderr=b"")
        fps = get_video_fps(fake)
    assert fps == pytest.approx(30.0)


def test_get_video_fps_fractional(tmp_path: Path) -> None:
    fake = tmp_path / "v.mp4"
    fake.write_bytes(b"x")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"30000/1001", stderr=b"")
        fps = get_video_fps(fake)
    assert fps == pytest.approx(30000 / 1001)


def test_get_video_fps_ffprobe_failure(tmp_path: Path) -> None:
    fake = tmp_path / "v.mp4"
    fake.write_bytes(b"x")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"no such file")
        with pytest.raises(VideoIOError):
            get_video_fps(fake)


def test_extract_frames_creates_files(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out_dir = tmp_path / "frames"

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=4)):
        frames, fps = extract_frames(video, out_dir)

    assert fps == pytest.approx(24.0)
    assert len(frames) == 4
    assert all(f.name.startswith("frame_") for f in frames)


def test_encode_frames_calls_ffmpeg(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    output = tmp_path / "out.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        encode_frames(frame_dir, output, fps=24.0)

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd[0]
    assert str(output) in cmd


def test_encode_frames_creates_parent_dir(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    output = tmp_path / "nested" / "deep" / "out.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        encode_frames(frame_dir, output, fps=24.0)

    assert output.parent.exists()


# ---------------------------------------------------------------------------
# _inject_input_image unit tests
# ---------------------------------------------------------------------------


def test_inject_input_image_replaces_filename() -> None:
    result = _inject_input_image(MINIMAL_WORKFLOW, "new_frame.png")
    assert result["1"]["inputs"]["image"] == "new_frame.png"


def test_inject_input_image_does_not_mutate_original() -> None:
    original_name = MINIMAL_WORKFLOW["1"]["inputs"]["image"]
    _inject_input_image(MINIMAL_WORKFLOW, "other.png")
    assert MINIMAL_WORKFLOW["1"]["inputs"]["image"] == original_name


def test_inject_input_image_no_load_node_raises() -> None:
    workflow_no_loader = {"1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}}
    with pytest.raises(PreprocessError, match="no LoadImage node"):
        _inject_input_image(workflow_no_loader, "x.png")


# ---------------------------------------------------------------------------
# preprocess_overlay — invalid style
# ---------------------------------------------------------------------------


def test_preprocess_overlay_invalid_style_raises(tmp_path: Path, fake_input_video: Path) -> None:
    with pytest.raises(ValueError, match="Unknown style"):
        import asyncio

        asyncio.run(
            preprocess_overlay(
                fake_input_video,
                tmp_path / "out.mp4",
                "watercolour",
            )
        )


def test_preprocess_overlay_missing_workflow_raises(tmp_path: Path, fake_input_video: Path) -> None:
    empty_wf_dir = tmp_path / "empty_workflows"
    empty_wf_dir.mkdir()
    with pytest.raises(PreprocessError, match="Workflow file not found"):
        import asyncio

        asyncio.run(
            preprocess_overlay(
                fake_input_video,
                tmp_path / "out.mp4",
                "lineart",
                workflows_dir=empty_wf_dir,
            )
        )


# ---------------------------------------------------------------------------
# preprocess_overlay — ComfyUI down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_preprocess_overlay_comfy_down(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    respx.post(f"{BASE}/upload/image").mock(side_effect=httpx.ConnectError("refused"))

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=2)):
        with pytest.raises(PreprocessError, match="ComfyUI error"):
            await preprocess_overlay(
                fake_input_video,
                tmp_path / "out.mp4",
                "lineart",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# preprocess_overlay — ComfyUI job error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_preprocess_overlay_job_error(
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
        with pytest.raises(PreprocessError, match="ComfyUI error"):
            await preprocess_overlay(
                fake_input_video,
                tmp_path / "out.mp4",
                "lineart",
                comfy_url=BASE,
                workflows_dir=workflow_dir,
            )


# ---------------------------------------------------------------------------
# preprocess_overlay — happy path (lineart)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_preprocess_overlay_happy_path(
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

    output = tmp_path / "out.mp4"

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        await preprocess_overlay(
            fake_input_video,
            output,
            "lineart",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )

    # ffmpeg encode was called (output parent created)
    assert output.parent.exists()


# ---------------------------------------------------------------------------
# preprocess_overlay — all four styles accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["lineart", "canny", "depth", "hed"])
@respx.mock
async def test_preprocess_overlay_all_styles(
    style: str, tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "f.png", "subfolder": "", "type": "input"})
    )
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json=HISTORY_OK))
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=FAKE_PNG))

    with patch("subprocess.run", side_effect=_make_subprocess_mock(tmp_path, frame_count=1)):
        await preprocess_overlay(
            fake_input_video,
            tmp_path / f"out_{style}.mp4",
            style,
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )


# ---------------------------------------------------------------------------
# preprocess_overlay — batch processing (>1 frame)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_preprocess_overlay_multi_frame_batch(
    tmp_path: Path, fake_input_video: Path, workflow_dir: Path
) -> None:
    """Three frames processed in a single batch (batch_size=16)."""
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
        await preprocess_overlay(
            fake_input_video,
            tmp_path / "out.mp4",
            "canny",
            comfy_url=BASE,
            workflows_dir=workflow_dir,
        )

    # upload+prompt+history(wait)+history(fetch)+view = 5 calls per frame
    assert respx.calls.call_count == frame_count * 5
