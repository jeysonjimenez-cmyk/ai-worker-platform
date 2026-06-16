from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

_TERMINAL = frozenset({"done", "error", "cancelled"})
_BACKOFF_MAX = 30.0


class JobTimeoutError(Exception):
    """Raised by wait() when the timeout expires before reaching a terminal state."""


class Client:
    def __init__(self, api_url: str, app_key: str, *, request_timeout: float = 30.0) -> None:
        self._base = api_url.rstrip("/")
        self._headers = {"X-App-Key": app_key}
        self._timeout = request_timeout

    def create_job(
        self,
        service: str,
        payload: dict[str, Any],
        *,
        priority: str | None = None,
        requirements: dict[str, Any] | None = None,
        routing: dict[str, Any] | None = None,
        webhook_url: str | None = None,
    ) -> str:
        """POST /ai/jobs with X-App-Key, returns job_id."""
        body: dict[str, Any] = {"service": service, "payload": payload}
        if priority is not None:
            body["priority"] = priority
        if requirements is not None:
            body["requirements"] = requirements
        if routing is not None:
            body["routing"] = routing
        if webhook_url is not None:
            body["webhook_url"] = webhook_url

        resp = httpx.post(
            f"{self._base}/ai/jobs",
            json=body,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def wait(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        interval: float = 2.0,
    ) -> dict[str, Any]:
        """Poll GET /ai/jobs/{id} with exponential backoff until terminal state.

        Raises JobTimeoutError if timeout (seconds) expires before the job finishes.
        Backoff: starts at interval, doubles each poll, capped at 30s.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        delay = interval

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise JobTimeoutError(
                    f"job {job_id} did not complete within {timeout}s"
                )

            resp = httpx.get(
                f"{self._base}/ai/jobs/{job_id}",
                headers=self._headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            job = resp.json()

            if job["status"] in _TERMINAL:
                return job

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JobTimeoutError(
                        f"job {job_id} did not complete within {timeout}s"
                    )
                sleep_for = min(delay, remaining)
            else:
                sleep_for = delay

            time.sleep(sleep_for)
            delay = min(delay * 2, _BACKOFF_MAX)

    def download(self, job_id: str, filename: str, dest: str | Path) -> None:
        """GET /ai/jobs/{id}/files/{filename} and write to dest by streaming.

        Raises RuntimeError if the API returns 503 (file server / ialab unreachable).
        Never buffers the file in memory.
        """
        dest = Path(dest)
        url = f"{self._base}/ai/jobs/{job_id}/files/{filename}"

        with httpx.Client(headers=self._headers) as client:
            with client.stream("GET", url, timeout=None) as resp:
                if resp.status_code == 503:
                    raise RuntimeError(
                        f"file server unavailable (503): job {job_id}, file {filename}"
                    )
                resp.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
