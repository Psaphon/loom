"""Tests for LoomComfyClient using respx to mock the ComfyUI HTTP API."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from loom.comfy import ComfyError, ComfyJobError, LoomComfyClient, _collect_file_refs

BASE = "http://127.0.0.1:8188"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WORKFLOW = {"node1": {"class_type": "KSampler", "inputs": {}}}

PROMPT_ID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"

HISTORY_COMPLETE = {
    PROMPT_ID: {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {
            "9": {
                "images": [
                    {"filename": "frame_0001.png", "subfolder": "", "type": "output"},
                    {"filename": "frame_0002.png", "subfolder": "", "type": "output"},
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
            "messages": [
                ["execution_error", {"exception_message": "CUDA out of memory"}],
            ],
        },
        "outputs": {},
    }
}


# ---------------------------------------------------------------------------
# submit()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_submit_returns_prompt_id() -> None:
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    async with LoomComfyClient(BASE) as client:
        job_id = await client.submit(WORKFLOW)
    assert job_id == PROMPT_ID


@pytest.mark.asyncio
@respx.mock
async def test_submit_wraps_raw_prompt() -> None:
    """If the caller passes a raw node dict (no 'prompt' key) it should be wrapped."""
    route = respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    async with LoomComfyClient(BASE) as client:
        await client.submit(WORKFLOW)

    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert "prompt" in body
    assert body["prompt"] == WORKFLOW


@pytest.mark.asyncio
@respx.mock
async def test_submit_already_wrapped() -> None:
    """If the caller pre-wraps in {'prompt': ...} it should not be double-wrapped."""
    route = respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": PROMPT_ID})
    )
    async with LoomComfyClient(BASE) as client:
        await client.submit({"prompt": WORKFLOW})

    import json

    body = json.loads(route.calls[0].request.content)
    # Outer key is 'prompt', not 'prompt' -> {'prompt': ...}
    assert body["prompt"] == WORKFLOW


@pytest.mark.asyncio
@respx.mock
async def test_submit_connection_refused_raises_comfy_error() -> None:
    respx.post(f"{BASE}/prompt").mock(side_effect=httpx.ConnectError("refused"))
    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="Cannot reach ComfyUI"):
            await client.submit(WORKFLOW)


@pytest.mark.asyncio
@respx.mock
async def test_submit_non_200_raises_comfy_error() -> None:
    respx.post(f"{BASE}/prompt").mock(return_value=httpx.Response(422, text="bad workflow"))
    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="rejected workflow"):
            await client.submit(WORKFLOW)


# ---------------------------------------------------------------------------
# wait()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_wait_returns_outputs_on_first_poll() -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETE)
    )
    async with LoomComfyClient(BASE) as client:
        outputs = await client.wait(PROMPT_ID, poll_interval=0)

    assert "9" in outputs
    assert len(outputs["9"]["images"]) == 2


@pytest.mark.asyncio
@respx.mock
async def test_wait_polls_until_complete() -> None:
    """First poll returns empty history; second returns the completed entry."""
    responses = [
        httpx.Response(200, json={}),
        httpx.Response(200, json=HISTORY_COMPLETE),
    ]
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(side_effect=responses)

    async with LoomComfyClient(BASE) as client:
        outputs = await client.wait(PROMPT_ID, poll_interval=0)

    assert outputs != {}


@pytest.mark.asyncio
@respx.mock
async def test_wait_raises_on_timeout() -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json={}))

    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="Timed out"):
            # timeout=0 means we exceed immediately on the first check
            await client.wait(PROMPT_ID, timeout=0, poll_interval=1)


@pytest.mark.asyncio
@respx.mock
async def test_wait_raises_comfy_job_error_on_failure() -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_ERROR)
    )
    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyJobError, match="CUDA out of memory"):
            await client.wait(PROMPT_ID, poll_interval=0)


@pytest.mark.asyncio
@respx.mock
async def test_wait_connection_lost_raises_comfy_error() -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(side_effect=httpx.ConnectError("gone"))
    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="Lost connection"):
            await client.wait(PROMPT_ID, poll_interval=0)


# ---------------------------------------------------------------------------
# fetch_outputs()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_outputs_downloads_files(tmp_path: Path) -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETE)
    )
    # Both /view calls return fake PNG bytes.
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=b"\x89PNG\r\n"))

    async with LoomComfyClient(BASE) as client:
        paths = await client.fetch_outputs(PROMPT_ID, tmp_path)

    assert len(paths) == 2
    for p in paths:
        assert p.exists()
        assert p.read_bytes() == b"\x89PNG\r\n"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_outputs_job_not_in_history(tmp_path: Path) -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(return_value=httpx.Response(200, json={}))
    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="not found in history"):
            await client.fetch_outputs(PROMPT_ID, tmp_path)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_outputs_creates_dest_dir(tmp_path: Path) -> None:
    dest = tmp_path / "new" / "subdir"
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETE)
    )
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=b"data"))

    async with LoomComfyClient(BASE) as client:
        paths = await client.fetch_outputs(PROMPT_ID, dest)

    assert dest.is_dir()
    assert len(paths) == 2


@pytest.mark.asyncio
@respx.mock
async def test_fetch_outputs_empty_when_no_files(tmp_path: Path) -> None:
    history_no_files = {
        PROMPT_ID: {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {},
        }
    }
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=history_no_files)
    )
    async with LoomComfyClient(BASE) as client:
        paths = await client.fetch_outputs(PROMPT_ID, tmp_path)
    assert paths == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_outputs_view_error_raises(tmp_path: Path) -> None:
    respx.get(f"{BASE}/history/{PROMPT_ID}").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETE)
    )
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(500, text="internal error"))

    async with LoomComfyClient(BASE) as client:
        with pytest.raises(ComfyError, match="/view error"):
            await client.fetch_outputs(PROMPT_ID, tmp_path)


# ---------------------------------------------------------------------------
# _collect_file_refs (unit)
# ---------------------------------------------------------------------------


def test_collect_file_refs_images() -> None:
    outputs = {
        "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
        "10": {"images": [{"filename": "b.png", "subfolder": "sub", "type": "output"}]},
    }
    refs = _collect_file_refs(outputs)
    assert len(refs) == 2
    assert {r["filename"] for r in refs} == {"a.png", "b.png"}


def test_collect_file_refs_gifs_and_videos() -> None:
    outputs = {
        "9": {
            "gifs": [{"filename": "out.gif", "subfolder": "", "type": "output"}],
            "videos": [{"filename": "out.mp4", "subfolder": "", "type": "output"}],
        }
    }
    refs = _collect_file_refs(outputs)
    assert len(refs) == 2


def test_collect_file_refs_empty() -> None:
    assert _collect_file_refs({}) == []
