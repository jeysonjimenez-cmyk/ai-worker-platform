from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

from agent.collector import MetricSample
from agent.reporter import Reporter, run_loop, _BUFFER_MAX, _BACKOFF_MAX


# ── helpers ──────────────────────────────────────────────────────────────────

def _sample(cpu: int = 30) -> MetricSample:
    return MetricSample(
        cpu_pct=cpu,
        ram_used_gb=8.0,
        recorded_at=datetime.now(timezone.utc),
    )


def _reporter(url: str = "http://vps:8080", buffer_max: int = _BUFFER_MAX) -> Reporter:
    return Reporter(url, "worker-key", "w-ialab", buffer_max=buffer_max)


class _FakeServer:
    """Minimal HTTP server that records received payloads and returns configurable status."""

    def __init__(self, port: int, status: int = 200) -> None:
        self.received: list[dict] = []
        self.status = status
        outer = self

        class _H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                outer.received.append(json.loads(body))
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"inserted":1}')

            def log_message(self, *_): pass

        self._httpd = HTTPServer(("127.0.0.1", port), _H)
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

    def shutdown(self):
        self._httpd.shutdown()


# ── buffer cap ───────────────────────────────────────────────────────────────

class TestBufferCap:
    def test_buffer_does_not_exceed_max(self):
        r = _reporter(buffer_max=5)
        for i in range(10):
            r.push(_sample(cpu=i))
        assert r.buffered == 5

    def test_oldest_are_dropped_when_full(self):
        r = _reporter(buffer_max=3)
        for i in range(5):
            r.push(_sample(cpu=i))
        # buffer holds the 3 most recent: cpu 2, 3, 4
        samples = list(r._buffer)
        assert samples[0].cpu_pct == 2
        assert samples[-1].cpu_pct == 4

    def test_default_buffer_max_is_bounded(self):
        assert _BUFFER_MAX > 0
        assert _BUFFER_MAX <= 1000


# ── flush success ────────────────────────────────────────────────────────────

class TestFlushSuccess:
    def test_flush_clears_buffer(self):
        _FakeServer(19200)
        r = _reporter("http://127.0.0.1:19200")
        r.push(_sample())
        result = r.flush()
        assert result is True
        assert r.buffered == 0

    def test_flush_sends_all_buffered_samples(self):
        srv = _FakeServer(19201)
        r = _reporter("http://127.0.0.1:19201")
        for i in range(3):
            r.push(_sample(cpu=i + 10))
        r.flush()
        assert len(srv.received) == 1
        assert len(srv.received[0]["samples"]) == 3

    def test_flush_preserves_recorded_at(self):
        srv = _FakeServer(19202)
        r = _reporter("http://127.0.0.1:19202")
        r.push(_sample())
        r.flush()
        s = srv.received[0]["samples"][0]
        assert "recorded_at" in s
        assert "T" in s["recorded_at"]

    def test_flush_resets_backoff_after_success(self):
        _FakeServer(19203)
        r = _reporter("http://127.0.0.1:19203")
        # simulate prior failures
        r._consecutive_failures = 3
        r.push(_sample())
        r.flush()
        assert r._consecutive_failures == 0

    def test_flush_empty_buffer_returns_true(self):
        r = _reporter()
        assert r.flush() is True

    def test_flush_sends_correct_worker_key_header(self):
        """X-Worker-Key header must be present in the POST."""
        received_headers = {}

        class _H(BaseHTTPRequestHandler):
            def do_POST(self):
                received_headers["X-Worker-Key"] = self.headers.get("X-Worker-Key", "")
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *_): pass

        httpd = HTTPServer(("127.0.0.1", 19204), _H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        r = Reporter("http://127.0.0.1:19204", "my-worker-key", "w-ialab")
        r.push(_sample())
        r.flush()

        httpd.shutdown()
        assert received_headers["X-Worker-Key"] == "my-worker-key"


# ── flush failure ────────────────────────────────────────────────────────────

class TestFlushFailure:
    def test_flush_returns_false_on_network_error(self):
        r = _reporter("http://127.0.0.1:19299")  # nothing listening
        r.push(_sample())
        result = r.flush()
        assert result is False

    def test_buffer_retained_on_failure(self):
        r = _reporter("http://127.0.0.1:19298")
        r.push(_sample())
        r.flush()
        assert r.buffered == 1

    def test_failure_increments_consecutive_count(self):
        r = _reporter("http://127.0.0.1:19297")
        r.push(_sample())
        r.flush()
        assert r._consecutive_failures == 1
        r.flush()
        assert r._consecutive_failures == 2

    def test_flush_never_raises(self):
        r = _reporter("http://not-a-valid-host:9999")
        r.push(_sample())
        r.flush()  # must not raise

    def test_flush_returns_false_on_500(self):
        _FakeServer(19205, status=500)
        r = _reporter("http://127.0.0.1:19205")
        r.push(_sample())
        result = r.flush()
        assert result is False


# ── backoff ──────────────────────────────────────────────────────────────────

class TestBackoff:
    def test_backoff_grows_exponentially(self):
        r = _reporter("http://127.0.0.1:19290")
        r._consecutive_failures = 1
        b1 = r.backoff_sec
        r._consecutive_failures = 2
        b2 = r.backoff_sec
        assert b2 == b1 * 2

    def test_backoff_capped_at_max(self):
        r = _reporter()
        r._consecutive_failures = 1000
        assert r.backoff_sec == _BACKOFF_MAX

    def test_backoff_resets_after_success(self):
        _FakeServer(19206)
        r = _reporter("http://127.0.0.1:19206")
        r._consecutive_failures = 5
        r.push(_sample())
        r.flush()
        assert r.backoff_sec < _BACKOFF_MAX


# ── run_loop ─────────────────────────────────────────────────────────────────

class TestRunLoop:
    def test_loop_calls_collect_and_flush(self):
        """run_loop calls collect, pushes, and flushes; stop after N iterations."""
        call_count = 0

        def _fake_collect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise StopIteration  # sentinel to break the loop in test
            return _sample()

        reporter = MagicMock()
        reporter.flush.return_value = True
        reporter.backoff_sec = 0

        with patch("agent.reporter.time.sleep"):
            try:
                run_loop(reporter, _fake_collect, interval_sec=0)
            except StopIteration:
                pass

        assert call_count == 3
        assert reporter.push.call_count == 2  # 3rd call raises before push

    def test_loop_does_not_raise_on_flush_failure(self):
        """Network failures must not propagate out of run_loop."""
        calls = [0]

        def _fake_collect():
            calls[0] += 1
            if calls[0] > 2:
                raise StopIteration
            return _sample()

        reporter = MagicMock()
        reporter.flush.return_value = False
        reporter.backoff_sec = 0

        with patch("agent.reporter.time.sleep"):
            try:
                run_loop(reporter, _fake_collect, interval_sec=0)
            except StopIteration:
                pass

    def test_loop_sleeps_backoff_on_failure(self):
        """When flush fails, loop sleeps backoff_sec, not interval_sec."""
        slept = []

        def _fake_collect():
            return _sample()

        reporter = MagicMock()
        reporter.flush.return_value = False
        reporter.backoff_sec = 42.0

        def _fake_sleep(n):
            slept.append(n)
            if len(slept) >= 1:
                raise StopIteration

        with patch("agent.reporter.time.sleep", _fake_sleep):
            try:
                run_loop(reporter, _fake_collect, interval_sec=10)
            except StopIteration:
                pass

        assert slept[0] == 42.0

    def test_loop_sleeps_interval_on_success(self):
        """When flush succeeds, loop sleeps interval_sec."""
        slept = []

        reporter = MagicMock()
        reporter.flush.return_value = True

        def _fake_sleep(n):
            slept.append(n)
            raise StopIteration

        with patch("agent.reporter.time.sleep", _fake_sleep):
            try:
                run_loop(reporter, _sample, interval_sec=10)
            except StopIteration:
                pass

        assert slept[0] == 10
