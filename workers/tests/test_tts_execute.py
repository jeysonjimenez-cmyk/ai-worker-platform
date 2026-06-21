"""
Tests for T7.5 — worker-tts execute() and model manager.
Higgs HTTP client is fully mocked (no GPU or model in CI).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest

import worker_tts as wt
from worker_tts import execute


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_model_state():
    """Reset module state and mock docker/higgs I/O so tests don't hit real hardware."""
    wt._model_loaded = False
    wt._active_jobs = 0
    wt._last_released = 0.0
    wt._worker_id = ""
    wt._unload_client = None
    _health_ok = MagicMock()
    _health_ok.is_success = True
    with patch("worker_tts.subprocess.run"), \
         patch("worker_tts.httpx.get", return_value=_health_ok):
        yield
    wt._model_loaded = False
    wt._active_jobs = 0


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx():
    c = MagicMock()
    c.report_progress = MagicMock()
    c.log = MagicMock()
    c.upload_file_metadata = MagicMock()
    return c


def _higgs_resp(audio: bytes = b"FAKEMP3") -> MagicMock:
    r = MagicMock()
    r.content = audio
    r.raise_for_status = MagicMock()
    r.is_success = True
    return r


def _job(text: str = "Hello world") -> dict:
    return {"id": "job-1", "service": "tts", "payload": {"text": text}}


# ── model manager ─────────────────────────────────────────────────────────────

def test_acquire_sets_loaded_and_increments():
    wt._acquire_model()
    assert wt._model_loaded is True
    assert wt._active_jobs == 1
    wt._release_model()


def test_acquire_increments_multiple():
    wt._acquire_model()
    wt._acquire_model()
    assert wt._active_jobs == 2
    wt._release_model()
    assert wt._active_jobs == 1
    wt._release_model()
    assert wt._active_jobs == 0


def test_release_decrements_and_sets_last_released():
    wt._active_jobs = 1
    before = time.monotonic()
    wt._release_model()
    assert wt._active_jobs == 0
    assert wt._last_released >= before


def test_execute_releases_model_on_success(tmp_path):
    with patch("worker_tts.httpx.post", return_value=_higgs_resp()), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        execute(_job(), _ctx())
    assert wt._active_jobs == 0


def test_execute_releases_model_on_error(tmp_path):
    with patch("worker_tts.httpx.post", side_effect=RuntimeError("higgs down")), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        with pytest.raises(RuntimeError, match="higgs down"):
            execute(_job(), _ctx())
    assert wt._active_jobs == 0


# ── execute: tts ──────────────────────────────────────────────────────────────

def test_execute_returns_files(tmp_path):
    with patch("worker_tts.httpx.post", return_value=_higgs_resp(b"AUDIO")), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        result = execute(_job("Synthesize this text"), _ctx())
    assert result == {"files": ["output.mp3"]}


def test_execute_writes_audio_file(tmp_path):
    audio_bytes = b"REALMP3DATA"
    with patch("worker_tts.httpx.post", return_value=_higgs_resp(audio_bytes)), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        execute(_job("some text"), _ctx())
    audio_file = tmp_path / "job-1" / "output.mp3"
    assert audio_file.exists()
    assert audio_file.read_bytes() == audio_bytes


def test_execute_registers_file_metadata(tmp_path):
    with patch("worker_tts.httpx.post", return_value=_higgs_resp(b"X" * 100)), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        ctx = _ctx()
        execute(_job("text"), ctx)
    ctx.upload_file_metadata.assert_called_once()
    args = ctx.upload_file_metadata.call_args
    assert args.args[0] == "output.mp3"
    assert "job-1" in args.args[1]  # path contains job_id
    assert args.kwargs.get("size_bytes", args.args[2] if len(args.args) > 2 else None) == 100


def test_execute_missing_text_raises(tmp_path):
    with patch("worker_tts.httpx.post"), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        with pytest.raises(ValueError, match="payload.text"):
            execute({"id": "job-1", "service": "tts", "payload": {}}, _ctx())


def test_execute_progress(tmp_path):
    ctx = _ctx()
    with patch("worker_tts.httpx.post", return_value=_higgs_resp()), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        execute(_job("text"), ctx)
    calls = [c.args[0] for c in ctx.report_progress.call_args_list]
    assert calls[0] == 10
    assert calls[-1] == 100


def test_execute_sends_text_to_higgs(tmp_path):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _higgs_resp()

    with patch("worker_tts.httpx.post", side_effect=fake_post), \
         patch("worker_tts._FILES_DIR", str(tmp_path)):
        execute(_job("Say something"), _ctx())

    assert "/v1/audio/speech" in captured["url"]
    assert captured["json"]["input"] == "Say something"
    assert captured["json"]["response_format"] == "mp3"


def test_execute_voice_cloning_sends_references(tmp_path):
    """With TTS_REFERENCE_AUDIO_PATH set, execute() sends references to Higgs."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _higgs_resp()

    with patch("worker_tts.httpx.post", side_effect=fake_post), \
         patch("worker_tts._FILES_DIR", str(tmp_path)), \
         patch("worker_tts._REFERENCE_AUDIO_PATH", "/refs/voice.wav"), \
         patch("worker_tts._REFERENCE_TEXT", "reference transcript"):
        execute(_job("Clone this voice"), _ctx())

    refs = captured["json"].get("references", [])
    assert len(refs) == 1
    assert refs[0]["audio_path"] == "/refs/voice.wav"
    assert refs[0]["text"] == "reference transcript"


def test_execute_no_references_when_path_empty(tmp_path):
    """Without TTS_REFERENCE_AUDIO_PATH, no references field is sent."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _higgs_resp()

    with patch("worker_tts.httpx.post", side_effect=fake_post), \
         patch("worker_tts._FILES_DIR", str(tmp_path)), \
         patch("worker_tts._REFERENCE_AUDIO_PATH", ""):
        execute(_job("Standard TTS"), _ctx())

    assert "references" not in captured["json"]


# ── idle watcher ──────────────────────────────────────────────────────────────

def test_idle_watcher_unloads_after_timeout():
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_client = MagicMock()
    mock_client.unload_model.return_value = mock_resp

    wt._model_loaded = True
    wt._active_jobs = 0
    wt._last_released = 0.0
    wt._unload_client = mock_client
    wt._worker_id = "w-tts-test"

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_tts.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wt._IDLE_TIMEOUT + 100
        try:
            wt._idle_watcher()
        except StopIteration:
            pass

    assert wt._model_loaded is False
    mock_client.unload_model.assert_called_once_with("w-tts-test")
    # Higgs unloaded via docker stop (subprocess.run mocked by autouse fixture)


def test_idle_watcher_skips_unload_when_active_job():
    wt._model_loaded = True
    wt._active_jobs = 1
    wt._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_tts.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wt._IDLE_TIMEOUT + 100
        try:
            wt._idle_watcher()
        except StopIteration:
            pass

    assert wt._model_loaded is True


def test_idle_watcher_skips_unload_before_timeout():
    wt._model_loaded = True
    wt._active_jobs = 0
    wt._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_tts.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wt._IDLE_TIMEOUT - 10
        try:
            wt._idle_watcher()
        except StopIteration:
            pass

    assert wt._model_loaded is True


def test_idle_watcher_not_loaded_skips():
    """Watcher does nothing if _model_loaded is False."""
    wt._model_loaded = False
    wt._active_jobs = 0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_tts.time") as mock_time, \
         patch("worker_tts.httpx.post") as mock_post:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wt._IDLE_TIMEOUT + 100
        try:
            wt._idle_watcher()
        except StopIteration:
            pass

    assert wt._model_loaded is False
    mock_post.assert_not_called()
