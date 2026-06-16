from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections import deque

from agent.collector import MetricSample

logger = logging.getLogger(__name__)

_BUFFER_MAX = 360          # ~1 hour at 10s intervals
_BACKOFF_BASE = 5.0        # seconds
_BACKOFF_MAX = 300.0       # 5 minutes cap


class Reporter:
    """Buffers MetricSamples and flushes them to the API in batches.

    Thread-safe for a single producer + single flusher pattern (the report loop).
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        worker_id: str,
        buffer_max: int = _BUFFER_MAX,
    ) -> None:
        self._url = f"{api_url}/workers/{worker_id}/metrics"
        self._headers = {
            "Content-Type": "application/json",
            "X-Worker-Key": api_key,
        }
        self._buffer: deque[MetricSample] = deque(maxlen=buffer_max)
        self._backoff = _BACKOFF_BASE
        self._consecutive_failures = 0

    def push(self, sample: MetricSample) -> None:
        """Add a sample to the buffer. Drops oldest if full (maxlen enforced by deque)."""
        self._buffer.append(sample)

    def flush(self) -> bool:
        """Attempt to send all buffered samples to the API.

        Returns True on success (buffer cleared), False on failure (buffer kept).
        Never raises.
        """
        if not self._buffer:
            return True

        samples = list(self._buffer)
        payload = json.dumps({"samples": [s.to_dict() for s in samples]}).encode()

        try:
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers=self._headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
            self._buffer.clear()
            self._consecutive_failures = 0
            self._backoff = _BACKOFF_BASE
            logger.debug("flushed %d sample(s)", len(samples))
            return True
        except Exception as exc:
            self._consecutive_failures += 1
            logger.warning(
                "flush failed (attempt %d, %d sample(s) buffered): %s",
                self._consecutive_failures,
                len(self._buffer),
                exc,
            )
            return False

    @property
    def backoff_sec(self) -> float:
        """Current backoff delay in seconds (doubles each failure, capped at _BACKOFF_MAX)."""
        delay = _BACKOFF_BASE * (2 ** (self._consecutive_failures - 1))
        return min(delay, _BACKOFF_MAX)

    @property
    def buffered(self) -> int:
        return len(self._buffer)


def run_loop(
    reporter: Reporter,
    collect_fn,
    interval_sec: int,
) -> None:
    """Collect + flush loop. Runs forever; never raises on network errors.

    collect_fn: callable returning MetricSample (injected for testability).
    On flush failure, waits backoff_sec before the next attempt rather than
    the normal interval, to avoid hammering a recovering VPS.
    """
    while True:
        sample = collect_fn()
        reporter.push(sample)

        if not reporter.flush():
            wait = reporter.backoff_sec
            logger.info("next retry in %.0fs", wait)
            time.sleep(wait)
        else:
            time.sleep(interval_sec)
