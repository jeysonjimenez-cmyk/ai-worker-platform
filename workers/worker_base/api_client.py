from __future__ import annotations

from typing import Any

import httpx


class APIClient:
    def __init__(self, api_url: str, worker_key: str, admin_key: str):
        self._base = api_url
        self._worker_headers = {
            "X-Worker-Key": worker_key,
            "Content-Type": "application/json",
        }
        self._admin_headers = {
            "X-Admin-Key": admin_key,
            "Content-Type": "application/json",
        }

    def register(
        self, worker_id: str, hostname: str, capabilities: dict, api_key: str
    ) -> httpx.Response:
        return httpx.post(
            f"{self._base}/workers/register",
            json={"id": worker_id, "hostname": hostname, "capabilities": capabilities, "api_key": api_key},
            headers=self._admin_headers,
            timeout=10,
        )

    def heartbeat(self, worker_id: str) -> httpx.Response:
        return httpx.post(
            f"{self._base}/workers/{worker_id}/heartbeat",
            headers=self._worker_headers,
            timeout=10,
        )

    def claim(self, worker_id: str, wait: int) -> httpx.Response:
        params: dict[str, Any] = {}
        if wait > 0:
            params["wait"] = wait
        return httpx.post(
            f"{self._base}/workers/{worker_id}/claim",
            params=params,
            headers=self._worker_headers,
            timeout=wait + 10,
        )

    def complete(
        self, job_id: str, result: dict | None, error: str | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {}
        if result is not None:
            body["result"] = result
        if error is not None:
            body["error"] = error
        return httpx.patch(
            f"{self._base}/ai/jobs/{job_id}/complete",
            json=body,
            headers=self._worker_headers,
            timeout=10,
        )

    def report_progress(self, job_id: str, progress: int) -> httpx.Response:
        return httpx.patch(
            f"{self._base}/ai/jobs/{job_id}/progress",
            json={"progress": progress},
            headers=self._worker_headers,
            timeout=10,
        )

    def log(self, job_id: str, message: str, level: str = "info") -> httpx.Response:
        return httpx.post(
            f"{self._base}/ai/jobs/{job_id}/logs",
            json={"logs": [{"level": level, "message": message}]},
            headers=self._worker_headers,
            timeout=10,
        )

    def upload_file_metadata(
        self, job_id: str, filename: str, path: str, size_bytes: int | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {"filename": filename, "path": path}
        if size_bytes is not None:
            body["size_bytes"] = size_bytes
        return httpx.post(
            f"{self._base}/ai/jobs/{job_id}/files",
            json=body,
            headers=self._worker_headers,
            timeout=10,
        )

    def unload_model(self, worker_id: str) -> httpx.Response:
        return httpx.post(
            f"{self._base}/workers/{worker_id}/unload-model",
            headers=self._worker_headers,
            timeout=10,
        )
