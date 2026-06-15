"""
Tests for T4.4 — transcription execute() with faster-whisper.
Tests for T4.5 — model manager: lazy load, idle unload.
faster-whisper and GPU are unavailable in CI; both are fully mocked.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Stub faster_whisper before importing worker_whisper so the lazy import inside
# _load_model() resolves to this module instead of requiring a real installation.
if "faster_whisper" not in sys.modules:
    _fw = types.ModuleType("faster_whisper")
    _fw.WhisperModel = MagicMock
    sys.modules["faster_whisper"] = _fw

import worker_whisper as ww
from worker_whisper import execute, _write_vtt, _write_srt, _write_json
from worker_whisper.downloader import SSRFError, FileTooLargeError


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_model_state():
    """Reset module-level model state before each test to prevent cross-test contamination."""
    ww._model = None
    ww._active_jobs = 0
    ww._last_released = 0.0
    ww._worker_id = ""
    ww._unload_client = None
    yield
    ww._model = None
    ww._active_jobs = 0


# ── test helpers ──────────────────────────────────────────────────────────────

def _seg(start: float, end: float, text: str):
    s = MagicMock()
    s.start = start
    s.end = end
    s.text = text
    return s


def _info(language: str = "en", duration: float = 10.0):
    i = MagicMock()
    i.language = language
    i.language_probability = 0.99
    i.duration = duration
    return i


def _ctx():
    c = MagicMock()
    c.report_progress = MagicMock()
    c.log = MagicMock()
    c.upload_file_metadata = MagicMock()
    return c


def _job(job_id: str = "job-1", audio_url: str = "https://example.com/audio.mp3"):
    return {"id": job_id, "service": "transcription", "payload": {"audio_url": audio_url}}


# ── T4.5: model manager ───────────────────────────────────────────────────────

def test_acquire_model_loads_when_none():
    """_acquire_model() calls WhisperModel when no model is cached."""
    mock = MagicMock()
    with patch("faster_whisper.WhisperModel", return_value=mock):
        result = ww._acquire_model()
    assert result is mock
    assert ww._model is mock
    assert ww._active_jobs == 1
    ww._release_model()


def test_acquire_model_reuses_cached():
    """_acquire_model() returns the cached model without reloading."""
    existing = MagicMock()
    ww._model = existing
    with patch("faster_whisper.WhisperModel") as fw_cls:
        result = ww._acquire_model()
    fw_cls.assert_not_called()
    assert result is existing
    assert ww._active_jobs == 1
    ww._release_model()


def test_release_model_decrements_active_jobs():
    """_release_model() decrements _active_jobs and sets _last_released."""
    ww._active_jobs = 1
    before = time.monotonic()
    ww._release_model()
    assert ww._active_jobs == 0
    assert ww._last_released >= before


def test_acquire_increments_release_decrements():
    """Nested acquire/release keeps _active_jobs correct (concurrency=1 in practice)."""
    mock = MagicMock()
    ww._model = mock
    ww._acquire_model()
    assert ww._active_jobs == 1
    ww._acquire_model()
    assert ww._active_jobs == 2
    ww._release_model()
    assert ww._active_jobs == 1
    ww._release_model()
    assert ww._active_jobs == 0


def test_execute_releases_model_on_success(tmp_path):
    """_active_jobs returns to 0 after a successful execute()."""
    segs = [_seg(0.0, 1.0, " text")]
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segs), _info())

    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=0), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        execute(_job(job_id="job-rel"), _ctx())

    assert ww._active_jobs == 0


def test_execute_releases_model_on_transcription_error(tmp_path):
    """_active_jobs returns to 0 even when transcription raises."""
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("CUDA OOM")

    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=0), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        with pytest.raises(RuntimeError, match="CUDA OOM"):
            execute(_job(job_id="job-oom"), _ctx())

    assert ww._active_jobs == 0


def test_execute_reloads_model_after_unload(tmp_path):
    """execute() loads the model if _model is None (simulates post-unload reload)."""
    segs = [_seg(0.0, 1.0, " hi")]
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segs), _info())

    ww._model = None  # simulate: model was unloaded
    with patch("faster_whisper.WhisperModel", return_value=mock_model) as fw_cls, \
         patch("worker_whisper.download", return_value=0), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        execute(_job(job_id="job-reload"), _ctx())

    fw_cls.assert_called_once()  # model was reloaded
    assert ww._active_jobs == 0


def test_idle_watcher_unloads_model_after_timeout():
    """Idle watcher sets _model=None and calls unload_model when timeout elapsed."""
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_client = MagicMock()
    mock_client.unload_model.return_value = mock_resp

    ww._model = MagicMock()
    ww._active_jobs = 0
    ww._last_released = 0.0  # long ago
    ww._unload_client = mock_client
    ww._worker_id = "w-idle-test"

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_whisper.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = ww._IDLE_TIMEOUT + 100
        try:
            ww._idle_watcher()
        except StopIteration:
            pass

    assert ww._model is None
    mock_client.unload_model.assert_called_once_with("w-idle-test")


def test_idle_watcher_skips_unload_when_active_job():
    """Watcher does not unload when _active_jobs > 0."""
    original_model = MagicMock()
    ww._model = original_model
    ww._active_jobs = 1
    ww._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_whisper.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = ww._IDLE_TIMEOUT + 100
        try:
            ww._idle_watcher()
        except StopIteration:
            pass

    assert ww._model is original_model


def test_idle_watcher_skips_unload_before_timeout():
    """Watcher does not unload if timeout has not elapsed."""
    original_model = MagicMock()
    ww._model = original_model
    ww._active_jobs = 0
    ww._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    # monotonic returns a value LESS than the timeout
    with patch("worker_whisper.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = ww._IDLE_TIMEOUT - 10
        try:
            ww._idle_watcher()
        except StopIteration:
            pass

    assert ww._model is original_model


# ── T4.4: execute() integration tests ────────────────────────────────────────

def test_execute_writes_vtt_srt_json(tmp_path):
    """execute() creates VTT, SRT, and JSON output files in files/{job_id}/."""
    segments = [_seg(0.0, 2.5, " Hello world"), _seg(2.5, 5.0, " How are you?")]
    info = _info(language="en", duration=5.0)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), info)

    ctx = _ctx()
    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=100), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        result = execute(_job(job_id="job-vtt"), ctx)

    files_dir = tmp_path / "job-vtt"
    assert (files_dir / "output.vtt").exists()
    assert (files_dir / "output.srt").exists()
    assert (files_dir / "output.json").exists()

    vtt = (files_dir / "output.vtt").read_text()
    assert "WEBVTT" in vtt
    assert "Hello world" in vtt
    assert "How are you?" in vtt

    data = json.loads((files_dir / "output.json").read_text())
    assert data["language"] == "en"
    assert len(data["segments"]) == 2

    assert result["language"] == "en"
    assert result["segments"] == 2
    assert result["files"] == ["output.vtt", "output.srt", "output.json"]


def test_execute_progress_advances_monotonically(tmp_path):
    """Progress must start at 5 (after download), advance monotonically, end at 100."""
    segments = [_seg(float(i), float(i + 1), f"seg {i}") for i in range(5)]
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), _info(duration=5.0))

    ctx = _ctx()
    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=0), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        execute(_job(job_id="job-prog"), ctx)

    calls = [c.args[0] for c in ctx.report_progress.call_args_list]
    assert calls[0] == 5, "first progress call must be 5 (post-download)"
    assert calls[-1] == 100, "last progress call must be 100"
    for a, b in zip(calls, calls[1:]):
        assert a <= b, f"progress went backwards: {calls}"


def test_execute_registers_three_files(tmp_path):
    """upload_file_metadata is called exactly once per output file (VTT, SRT, JSON)."""
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([_seg(0.0, 1.0, " text")]), _info())

    ctx = _ctx()
    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=0), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        execute(_job(job_id="job-files"), ctx)

    assert ctx.upload_file_metadata.call_count == 3
    registered = {c.args[0] for c in ctx.upload_file_metadata.call_args_list}
    assert registered == {"output.vtt", "output.srt", "output.json"}


def test_execute_ssrf_blocked_raises(tmp_path):
    """An audio_url rejected by SSRF policy makes execute() raise ValueError."""
    ctx = _ctx()
    with patch("faster_whisper.WhisperModel"), \
         patch("worker_whisper.download", side_effect=SSRFError("blocked")), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        with pytest.raises(ValueError, match="SSRF policy"):
            execute(_job(audio_url="http://192.168.1.1/evil.mp3"), ctx)


def test_execute_file_too_large_raises(tmp_path):
    """An oversized audio file makes execute() raise ValueError."""
    ctx = _ctx()
    with patch("faster_whisper.WhisperModel"), \
         patch("worker_whisper.download", side_effect=FileTooLargeError("too big")), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        with pytest.raises(ValueError, match="too large"):
            execute(_job(), ctx)


def test_execute_passes_configured_max_bytes(tmp_path):
    """download() receives the AUDIO_MAX_SIZE_BYTES limit, not just the default."""
    segs = [_seg(0.0, 1.0, " text")]
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segs), _info())

    with patch("faster_whisper.WhisperModel", return_value=mock_model), \
         patch("worker_whisper.download", return_value=0) as dl, \
         patch("worker_whisper._AUDIO_MAX_BYTES", 12345), \
         patch("worker_whisper._FILES_DIR", str(tmp_path)):
        execute(_job(job_id="job-max"), _ctx())

    assert dl.call_args.kwargs["max_bytes"] == 12345


# ── _write_vtt / _write_srt / _write_json unit tests ─────────────────────────

def test_write_vtt_format(tmp_path):
    segs = [_seg(0.0, 2.5, " Hello"), _seg(2.5, 5.0, " World")]
    path = tmp_path / "out.vtt"
    _write_vtt(segs, path)
    content = path.read_text()
    assert content.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:02.500" in content
    assert "Hello" in content
    assert "World" in content


def test_write_srt_format(tmp_path):
    segs = [_seg(0.0, 2.5, " Hello"), _seg(2.5, 5.0, " World")]
    path = tmp_path / "out.srt"
    _write_srt(segs, path)
    content = path.read_text()
    assert "1\n" in content
    assert "00:00:00,000 --> 00:00:02,500" in content
    assert "2\n" in content
    assert "Hello" in content


def test_write_json_structure(tmp_path):
    segs = [_seg(0.0, 2.5, " Hello")]
    info = _info(language="es", duration=2.5)
    path = tmp_path / "out.json"
    _write_json(segs, info, path)
    data = json.loads(path.read_text())
    assert data["language"] == "es"
    assert data["duration"] == 2.5
    assert data["segments"][0] == {"start": 0.0, "end": 2.5, "text": "Hello"}
