from __future__ import annotations

import logging
import signal
import threading
from typing import Any, Callable

from .api_client import APIClient
from .config import Config

logger = logging.getLogger(__name__)

ExecuteFn = Callable[["dict", "JobContext"], Any]


class JobContext:
    """Passed to execute(job, ctx). Provides log() and report_progress() without HTTP knowledge."""

    def __init__(self, job_id: str, client: APIClient) -> None:
        self._job_id = job_id
        self._client = client

    def report_progress(self, pct: int) -> None:
        try:
            resp = self._client.report_progress(self._job_id, pct)
        except Exception as exc:
            logger.warning("report_progress failed for job %s: %s", self._job_id, exc)
            return
        if resp.status_code == 409:
            logger.warning("fencing: progress report discarded for job %s", self._job_id)
        elif not resp.is_success:
            logger.warning(
                "report_progress unexpected status %d for job %s", resp.status_code, self._job_id
            )

    def log(self, message: str, level: str = "info") -> None:
        try:
            resp = self._client.log(self._job_id, message, level)
        except Exception as exc:
            logger.warning("log failed for job %s: %s", self._job_id, exc)
            return
        if resp.status_code == 409:
            logger.warning("fencing: log discarded for job %s", self._job_id)
        elif not resp.is_success:
            logger.warning(
                "log unexpected status %d for job %s", resp.status_code, self._job_id
            )


class Scaffold:
    """
    Lifecycle scaffold for AI workers.

    Workers implement only execute(job, ctx) -> result. Everything else —
    registration, heartbeat, claim loop, fencing, graceful shutdown — lives here.

    Usage:
        cfg = from_env()
        scaffold = Scaffold(cfg, execute=my_execute)
        scaffold.run()  # blocks until SIGTERM or stop()
    """

    def __init__(self, config: Config, execute: ExecuteFn) -> None:
        self._cfg = config
        self._execute = execute
        self._client = APIClient(config.api_url, config.worker_key, config.admin_key)
        self._stop = threading.Event()

    # ─── T3.2: registration ───────────────────────────────────────────────────

    def register(self) -> None:
        resp = self._client.register(
            worker_id=self._cfg.worker_id,
            hostname=self._cfg.hostname,
            capabilities=self._cfg.capabilities,
            api_key=self._cfg.worker_key,
        )
        resp.raise_for_status()
        logger.info("registered worker %s on %s", self._cfg.worker_id, self._cfg.hostname)

    # ─── T3.3: heartbeat thread ───────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        # wait() returns True when _stop is set, False on timeout.
        # Loop runs while wait() returns False (i.e., stop not yet requested).
        while not self._stop.wait(self._cfg.heartbeat_interval):
            try:
                resp = self._client.heartbeat(self._cfg.worker_id)
                if not resp.is_success:
                    logger.warning("heartbeat non-200: %d", resp.status_code)
            except Exception as exc:
                logger.warning("heartbeat error (will retry): %s", exc)

    # ─── T3.4 + T3.5: claim loop with long-poll, backoff, and fencing ─────────

    def _complete_job(self, job_id: str, result: dict | None, error: str | None = None) -> None:
        """Report job outcome. On 409 (fencing), discard silently and return."""
        try:
            resp = self._client.complete(job_id, result, error)
        except Exception as exc:
            logger.error("complete request failed for job %s: %s", job_id, exc)
            return

        # T3.5: fencing — job was reassigned to another worker while we ran.
        if resp.status_code == 409:
            logger.warning(
                "fencing: job %s no longer owned by this worker — discarding result", job_id
            )
            return

        if not resp.is_success:
            logger.error("complete unexpected status %d for job %s", resp.status_code, job_id)

    def _run_claim_loop(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                resp = self._client.claim(self._cfg.worker_id, wait=self._cfg.poll_wait)
            except Exception as exc:
                logger.warning("claim error (retrying in %ds): %s", backoff, exc)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self._cfg.backoff_max)
                continue

            backoff = 1  # reset on any successful HTTP response

            if resp.status_code == 204:
                # No job available — the long-poll already waited; pause briefly before retrying.
                self._stop.wait(2)
                continue

            if not resp.is_success:
                logger.error("claim unexpected status %d: %s", resp.status_code, resp.text[:200])
                self._stop.wait(5)
                continue

            job = resp.json()
            job_id = job["id"]
            logger.info("claimed job %s (service=%s)", job_id, job.get("service"))

            ctx = JobContext(job_id, self._client)
            try:
                raw = self._execute(job, ctx)
                result = raw if isinstance(raw, dict) else {"output": raw}
                self._complete_job(job_id, result)
            except Exception as exc:
                logger.error("execute raised for job %s: %s", job_id, exc)
                self._complete_job(job_id, None, error=str(exc))

    # ─── Entry point ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Register, install SIGTERM handler, start heartbeat thread, run claim loop."""
        self.register()

        # T3.8: SIGTERM → stop cleanly. The heartbeat monitor re-queues any running job
        # automatically after its 90s timeout — no explicit "return to pending" endpoint exists.
        def _sigterm(signum: int, frame: Any) -> None:
            logger.info("SIGTERM received — stopping worker %s", self._cfg.worker_id)
            self._stop.set()

        signal.signal(signal.SIGTERM, _sigterm)

        hb = threading.Thread(target=self._heartbeat_loop, daemon=True, name="heartbeat")
        hb.start()
        logger.info("worker %s started", self._cfg.worker_id)

        try:
            self._run_claim_loop()
        finally:
            self._stop.set()
            hb.join(timeout=self._cfg.heartbeat_interval + 2)

    def stop(self) -> None:
        self._stop.set()
