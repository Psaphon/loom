"""Async HTTP client for the ComfyUI API.

Handles workflow submission, job polling, and output fetching.
ComfyUI is an external dependency — loom assumes it is already running.

API surface used:
  POST /prompt           — queue a workflow
  GET  /history/{id}    — poll for completion / fetch output metadata
  GET  /view             — download a single output file
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# How long (seconds) to wait between poll attempts.
_POLL_INTERVAL = 3.0


class ComfyError(Exception):
    """Raised when ComfyUI returns an error or is unreachable."""


class ComfyJobError(ComfyError):
    """Raised when a submitted job fails inside ComfyUI."""

    def __init__(self, job_id: str, detail: str) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} failed: {detail}")


class LoomComfyClient:
    """Async client for the ComfyUI HTTP API.

    Parameters
    ----------
    base_url:
        Base URL of the running ComfyUI instance, e.g. ``http://127.0.0.1:8188``.
    timeout:
        Default per-job timeout in seconds.  Can be overridden in ``wait()``.
    client_id:
        Optional stable client identifier sent with every prompt.  A random
        UUID is generated per-instance when omitted.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        timeout: float = 3600.0,
        client_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = timeout
        self.client_id = client_id or str(uuid.uuid4())
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, workflow_json: dict[str, Any]) -> str:
        """Queue a workflow and return the prompt ID (job ID).

        Parameters
        ----------
        workflow_json:
            A ComfyUI *API-format* workflow dict (``{"prompt": {...}}``) or
            just the inner prompt dict — loom wraps it automatically.

        Returns
        -------
        str
            The ``prompt_id`` assigned by ComfyUI.

        Raises
        ------
        ComfyError
            If ComfyUI is unreachable or returns a non-200 response.
        """
        # Accept either {"prompt": {...}} or the raw node dict.
        if "prompt" not in workflow_json:
            payload: dict[str, Any] = {"prompt": workflow_json, "client_id": self.client_id}
        else:
            payload = {**workflow_json, "client_id": self.client_id}

        logger.debug("Submitting workflow to ComfyUI (client_id=%s)", self.client_id)
        try:
            resp = await self._http.post("/prompt", json=payload)
        except httpx.ConnectError as exc:
            raise ComfyError(
                f"Cannot reach ComfyUI at {self.base_url} — is it running? ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyError(f"HTTP error submitting workflow: {exc}") from exc

        if resp.status_code != 200:
            raise ComfyError(
                f"ComfyUI rejected workflow (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        data = resp.json()
        job_id: str = data["prompt_id"]
        logger.info("Submitted job job_id=%s", job_id)
        return job_id

    async def wait(
        self,
        job_id: str,
        timeout: float | None = None,
        *,
        poll_interval: float = _POLL_INTERVAL,
    ) -> dict[str, Any]:
        """Poll ``/history/{job_id}`` until the job is complete.

        Parameters
        ----------
        job_id:
            Prompt ID returned by :meth:`submit`.
        timeout:
            Seconds to wait before raising :class:`ComfyError`.  Defaults to
            the instance-level ``timeout`` passed to the constructor.
        poll_interval:
            Seconds between poll attempts.

        Returns
        -------
        dict
            The ``outputs`` dict from the completed history entry.

        Raises
        ------
        ComfyError
            On timeout or HTTP error.
        ComfyJobError
            If ComfyUI reports that the job encountered an error.
        """
        import asyncio

        effective_timeout = timeout if timeout is not None else self.default_timeout
        elapsed = 0.0

        logger.info("Waiting for job job_id=%s (timeout=%.0fs)", job_id, effective_timeout)

        while True:
            if elapsed >= effective_timeout:
                raise ComfyError(
                    f"Timed out waiting for job {job_id} after {effective_timeout:.0f}s"
                )

            try:
                resp = await self._http.get(f"/history/{job_id}")
            except httpx.ConnectError as exc:
                raise ComfyError(
                    f"Lost connection to ComfyUI while polling job {job_id}: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise ComfyError(f"HTTP error polling job {job_id}: {exc}") from exc

            if resp.status_code != 200:
                raise ComfyError(
                    f"ComfyUI history error (HTTP {resp.status_code}): {resp.text[:200]}"
                )

            history = resp.json()

            if job_id in history:
                entry = history[job_id]
                self._raise_if_failed(job_id, entry)
                outputs = entry.get("outputs", {})
                logger.info("Job complete job_id=%s", job_id)
                return outputs

            # Job not yet in history — keep polling.
            logger.debug("Polling job_id=%s elapsed=%.0fs", job_id, elapsed)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    async def fetch_outputs(self, job_id: str, dest_dir: Path) -> list[Path]:
        """Download all output files for a completed job.

        Calls ``/view`` for each image/video file listed in the job's history
        outputs and writes them into *dest_dir*.

        Parameters
        ----------
        job_id:
            Prompt ID of the completed job.
        dest_dir:
            Directory to write downloaded files into.  Created if absent.

        Returns
        -------
        list[Path]
            Paths of the downloaded files.

        Raises
        ------
        ComfyError
            If the job has not completed yet or a download fails.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Re-fetch history to get output metadata.
        try:
            resp = await self._http.get(f"/history/{job_id}")
        except httpx.ConnectError as exc:
            raise ComfyError(
                f"Cannot reach ComfyUI while fetching outputs for job {job_id}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyError(f"HTTP error fetching history for job {job_id}: {exc}") from exc

        if resp.status_code != 200:
            raise ComfyError(f"ComfyUI history error (HTTP {resp.status_code}): {resp.text[:200]}")

        history = resp.json()
        if job_id not in history:
            raise ComfyError(f"Job {job_id} not found in history — has it completed?")

        entry = history[job_id]
        self._raise_if_failed(job_id, entry)

        file_refs = _collect_file_refs(entry.get("outputs", {}))
        if not file_refs:
            logger.warning("No output files found for job job_id=%s", job_id)
            return []

        downloaded: list[Path] = []
        for ref in file_refs:
            path = await self._download_file(ref, dest_dir)
            downloaded.append(path)

        logger.info(
            "Fetched %d output file(s) for job_id=%s to %s",
            len(downloaded),
            job_id,
            dest_dir,
        )
        return downloaded

    async def upload_image(self, path: Path) -> str:
        """Upload a local image file to ComfyUI's input directory.

        Parameters
        ----------
        path:
            Local image file to upload (typically a PNG frame).

        Returns
        -------
        str
            The filename as assigned by ComfyUI (use this in workflow inputs).

        Raises
        ------
        ComfyError
            If ComfyUI is unreachable or returns a non-200 response.
        """
        image_bytes = path.read_bytes()
        logger.debug("Uploading image filename=%s", path.name)
        try:
            resp = await self._http.post(
                "/upload/image",
                files={"image": (path.name, image_bytes, "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
        except httpx.ConnectError as exc:
            raise ComfyError(
                f"Cannot reach ComfyUI at {self.base_url} — is it running? ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyError(f"HTTP error uploading image {path.name}: {exc}") from exc

        if resp.status_code != 200:
            raise ComfyError(f"ComfyUI upload failed (HTTP {resp.status_code}): {resp.text[:200]}")

        filename: str = resp.json()["name"]
        logger.debug("Uploaded %s -> %s", path.name, filename)
        return filename

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # Support use as an async context manager.
    async def __aenter__(self) -> LoomComfyClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_if_failed(self, job_id: str, entry: dict[str, Any]) -> None:
        """Raise ComfyJobError if the history entry records an execution error."""
        status_info = entry.get("status", {})
        # ComfyUI sets status_str to "error" and populates "messages" with details.
        if status_info.get("status_str") == "error":
            messages = status_info.get("messages", [])
            # Messages are [type, payload] pairs; collect any "execution_error" payloads.
            details: list[str] = []
            for msg in messages:
                if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                    if msg[0] == "execution_error":
                        payload = msg[1]
                        details.append(
                            payload.get("exception_message", str(payload))
                            if isinstance(payload, dict)
                            else str(payload)
                        )
            detail = "; ".join(details) if details else "unknown error"
            raise ComfyJobError(job_id, detail)

    async def _download_file(self, ref: dict[str, str], dest_dir: Path) -> Path:
        """Download a single file reference from ``/view`` into *dest_dir*."""
        params = {k: v for k, v in ref.items() if k in ("filename", "subfolder", "type")}
        filename = ref["filename"]
        dest = dest_dir / filename

        logger.debug("Downloading output file filename=%s", filename)
        try:
            resp = await self._http.get("/view", params=params)
        except httpx.HTTPError as exc:
            raise ComfyError(f"Failed to download output {filename}: {exc}") from exc

        if resp.status_code != 200:
            raise ComfyError(
                f"ComfyUI /view error for {filename} (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        dest.write_bytes(resp.content)
        logger.debug("Saved output file path=%s", dest)
        return dest


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _collect_file_refs(outputs: dict[str, Any]) -> list[dict[str, str]]:
    """Walk a job's outputs dict and collect all image/GIF/video file refs."""
    refs: list[dict[str, str]] = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in ("images", "gifs", "videos"):
            for item in node_output.get(key, []):
                if isinstance(item, dict) and "filename" in item:
                    refs.append(item)
    return refs
