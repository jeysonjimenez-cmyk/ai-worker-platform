"""
Tests for T3.2 (registration), T3.3 (heartbeat), T3.4 (claim loop), T3.5 (fencing),
T3.7 (helpers: report_progress, log), T3.8 (graceful shutdown via SIGTERM).
API calls are mocked — no real server needed.
"""
import os
import signal
import threading
import time
from typing import Any
from unittest.mock import MagicMock


from worker_base.config import Config
from worker_base.scaffold import Scaffold


def _cfg(**overrides) -> Config:
    defaults = dict(
        api_url="http://localhost:8080",
        worker_key="wk",
        admin_key="ak",
        worker_id="w-test",
        hostname="testhost",
        capabilities={"services": ["echo"]},
        heartbeat_interval=1,
        poll_wait=0,
        backoff_max=1,
    )
    defaults.update(overrides)
    return Config(**defaults)


class FakeResp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body or {}
        self.text = str(body)
        self.is_success = status < 300

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ─── T3.2: registration ───────────────────────────────────────────────────────

def test_register_called_at_startup():
    scaffold = Scaffold(_cfg(), execute=lambda job, ctx: {})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))

    call_count = [0]

    def claim_fn(worker_id, wait):
        call_count[0] += 1
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    scaffold._client.register.assert_called_once()
    _, kwargs = scaffold._client.register.call_args
    assert kwargs["worker_id"] == "w-test"
    assert kwargs["hostname"] == "testhost"
    assert kwargs["api_key"] == "wk"


def test_register_idempotent_no_error_on_200():
    """Re-registration (server returns 200 again) must not raise."""
    scaffold = Scaffold(_cfg(), execute=lambda job, ctx: {})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.claim = MagicMock(
        side_effect=lambda *a, **k: (scaffold._stop.set() or FakeResp(204))
    )
    scaffold.run()
    assert scaffold._client.register.call_count == 1


# ─── T3.3: heartbeat fires independently of execute() ────────────────────────

def test_heartbeat_fires_while_execute_blocked():
    """Heartbeat must fire ≥2 times during a 2.5s execute() with 1s interval."""
    heartbeat_times: list[float] = []
    execute_start: list[float] = []
    execute_end: list[float] = []

    def slow_execute(job, ctx):
        execute_start.append(time.monotonic())
        time.sleep(2.5)
        execute_end.append(time.monotonic())
        return {}

    scaffold = Scaffold(_cfg(heartbeat_interval=1), execute=slow_execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(
        side_effect=lambda _: heartbeat_times.append(time.monotonic()) or FakeResp(200)
    )
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j1", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert execute_start, "execute was never called"
    hb_during = [t for t in heartbeat_times if execute_start[0] < t < execute_end[0]]
    assert len(hb_during) >= 2, f"expected >=2 heartbeats during execute, got {len(hb_during)}"


def test_heartbeat_survives_individual_failure():
    """A heartbeat HTTP error must not kill the heartbeat thread."""
    heartbeat_calls: list[int] = []
    call_n = [0]

    def hb_fn(_):
        call_n[0] += 1
        heartbeat_calls.append(call_n[0])
        if call_n[0] == 1:
            raise Exception("network error")
        return FakeResp(200)

    scaffold = Scaffold(_cfg(heartbeat_interval=1), execute=lambda job, ctx: {})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(side_effect=hb_fn)
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))

    call_count = [0]

    def claim_fn(worker_id, wait):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeResp(200, {"id": "j2", "service": "echo", "payload": {}})
        # Stop after a second job-less round (heartbeat had time to fire twice)
        if call_n[0] >= 2:
            scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert call_n[0] >= 2, "expected heartbeat to be called again after failure"


# ─── T3.4: claim loop ────────────────────────────────────────────────────────

def test_claim_loop_calls_execute_and_complete():
    executed = []
    completed = []

    def execute(job, ctx):
        executed.append(job["id"])
        return {"echo": job["payload"]}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(
        side_effect=lambda jid, result, error=None: completed.append(jid) or FakeResp(200)
    )

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "job-42", "service": "echo", "payload": {"text": "hi"}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert "job-42" in executed
    assert "job-42" in completed


def test_claim_loop_retries_on_network_error():
    """A network error in claim must not terminate the loop."""
    claim_attempts = [0]

    def claim_fn(worker_id, wait):
        claim_attempts[0] += 1
        if claim_attempts[0] == 1:
            raise Exception("connection refused")
        scaffold._stop.set()
        return FakeResp(204)

    scaffold = Scaffold(_cfg(backoff_max=1), execute=lambda job, ctx: {})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert claim_attempts[0] >= 2, "expected at least one retry after network error"


def test_execute_error_reported_as_complete_error():
    """If execute() raises, the job must be completed with error= instead of crashing."""
    complete_calls = []

    def bad_execute(job, ctx):
        raise ValueError("model failed")

    scaffold = Scaffold(_cfg(), execute=bad_execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(
        side_effect=lambda jid, result, error=None: complete_calls.append({"jid": jid, "error": error})
        or FakeResp(200)
    )

    call_count = [0]

    def claim_fn(worker_id, wait):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeResp(200, {"id": "j-err", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert len(complete_calls) == 1
    assert complete_calls[0]["jid"] == "j-err"
    assert "model failed" in complete_calls[0]["error"]


# ─── T3.5: fencing (409) ─────────────────────────────────────────────────────

def test_fencing_409_discards_and_continues():
    """A 409 from /complete must be silently discarded; the worker stays alive."""
    complete_calls = [0]
    claim_calls = [0]

    def execute(job, ctx):
        return {"output": "result"}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))

    def complete_fn(job_id, result, error=None):
        complete_calls[0] += 1
        return FakeResp(409)

    scaffold._client.complete = MagicMock(side_effect=complete_fn)

    def claim_fn(worker_id, wait):
        claim_calls[0] += 1
        if claim_calls[0] == 1:
            return FakeResp(200, {"id": "j-fenced", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    # Worker must have survived (reached second claim call) and not raised.
    assert claim_calls[0] >= 2


def test_fencing_409_does_not_retry():
    """After a 409 fencing response, the worker must NOT retry the same report."""
    complete_calls = [0]

    scaffold = Scaffold(_cfg(), execute=lambda job, ctx: {"output": "x"})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))

    def complete_fn(job_id, result, error=None):
        complete_calls[0] += 1
        return FakeResp(409)

    scaffold._client.complete = MagicMock(side_effect=complete_fn)

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-409", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert complete_calls[0] == 1, "must not retry after 409"


# ─── T3.7: helpers report_progress() and log() ───────────────────────────────

def test_report_progress_calls_api():
    """report_progress() must call the progress endpoint and handle 409 without crashing."""
    progress_calls = []

    def execute(job, ctx):
        ctx.report_progress(50)
        return {}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.report_progress = MagicMock(
        side_effect=lambda jid, pct: progress_calls.append((jid, pct)) or FakeResp(200)
    )

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-prog", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert ("j-prog", 50) in progress_calls


def test_report_progress_409_does_not_crash():
    """A 409 from report_progress must be discarded; the worker continues."""
    scaffold = Scaffold(_cfg(), execute=lambda job, ctx: (ctx.report_progress(50), {})[1])
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.report_progress = MagicMock(return_value=FakeResp(409))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-fp", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()  # must not raise


def test_log_helper_calls_api():
    """log() must call the log endpoint and handle 409 without crashing."""
    log_calls = []

    def execute(job, ctx):
        ctx.log("hello from test")
        return {}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.log = MagicMock(
        side_effect=lambda jid, msg, lvl="info": log_calls.append((jid, msg)) or FakeResp(201)
    )

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-log", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert ("j-log", "hello from test") in log_calls


def test_log_helper_409_does_not_crash():
    """A 409 from log() must be discarded; the worker continues."""
    scaffold = Scaffold(_cfg(), execute=lambda job, ctx: (ctx.log("msg"), {})[1])
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.log = MagicMock(return_value=FakeResp(409))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-lf", "service": "echo", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()  # must not raise


# ─── T4.3: upload_file_metadata() ────────────────────────────────────────────

def test_upload_file_metadata_calls_api():
    """upload_file_metadata() must call the files endpoint."""
    file_calls = []

    def execute(job, ctx):
        ctx.upload_file_metadata("output.vtt", f"files/{job['id']}/output.vtt", size_bytes=1234)
        return {}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.upload_file_metadata = MagicMock(
        side_effect=lambda jid, fname, path, size=None: file_calls.append(
            {"job_id": jid, "filename": fname, "path": path, "size_bytes": size}
        ) or FakeResp(201)
    )

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-file", "service": "transcription", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()

    assert len(file_calls) == 1
    assert file_calls[0]["filename"] == "output.vtt"
    assert file_calls[0]["size_bytes"] == 1234


def test_upload_file_metadata_409_does_not_crash():
    """A 409 from upload_file_metadata must be discarded; the worker continues."""

    def execute(job, ctx):
        ctx.upload_file_metadata("output.vtt", "files/j/output.vtt")
        return {}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.upload_file_metadata = MagicMock(return_value=FakeResp(409))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-fmeta", "service": "transcription", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()  # must not raise


def test_upload_file_metadata_network_error_does_not_crash():
    """A network error in upload_file_metadata must be caught; the worker continues."""

    def execute(job, ctx):
        ctx.upload_file_metadata("output.vtt", "files/j/output.vtt")
        return {}

    scaffold = Scaffold(_cfg(), execute=execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))
    scaffold._client.upload_file_metadata = MagicMock(side_effect=Exception("connection refused"))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-fnet", "service": "transcription", "payload": {}})
        scaffold._stop.set()
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)
    scaffold.run()  # must not raise


# ─── T3.8: graceful shutdown via SIGTERM ─────────────────────────────────────

def test_sigterm_stops_worker_with_no_job():
    """SIGTERM when idle stops the worker cleanly."""
    scaffold = Scaffold(_cfg(heartbeat_interval=1), execute=lambda job, ctx: {})
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))

    claim_called = [0]

    def claim_fn(worker_id, wait):
        claim_called[0] += 1
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)

    def _send_sigterm():
        time.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)

    t = threading.Thread(target=_send_sigterm, daemon=True)
    t.start()
    scaffold.run()  # must return after SIGTERM
    assert scaffold._stop.is_set()


def test_sigterm_stops_worker_during_execute():
    """SIGTERM while execute() is running — heartbeat and loop stop cleanly after the job."""
    execute_completed = [False]

    def slow_execute(job, ctx):
        time.sleep(0.3)
        execute_completed[0] = True
        return {"output": "done"}

    scaffold = Scaffold(_cfg(heartbeat_interval=1), execute=slow_execute)
    scaffold._client.register = MagicMock(return_value=FakeResp(200))
    scaffold._client.heartbeat = MagicMock(return_value=FakeResp(200))
    scaffold._client.complete = MagicMock(return_value=FakeResp(200))

    claim_count = [0]

    def claim_fn(worker_id, wait):
        claim_count[0] += 1
        if claim_count[0] == 1:
            return FakeResp(200, {"id": "j-sig", "service": "echo", "payload": {}})
        return FakeResp(204)

    scaffold._client.claim = MagicMock(side_effect=claim_fn)

    def _send_sigterm():
        time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    t = threading.Thread(target=_send_sigterm, daemon=True)
    t.start()
    scaffold.run()

    # execute() ran to completion; worker stopped cleanly.
    assert execute_completed[0], "execute was interrupted before finishing"
    assert scaffold._stop.is_set()
