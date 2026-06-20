"""
Tests for T6.4 — ollama execute() for translation and llm_chat.
Tests for T6.4 — model manager: acquire/release, idle unload.
Ollama HTTP client is fully mocked (no GPU or model in CI).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import worker_ollama as wo
from worker_ollama import execute


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_model_state():
    wo._model_loaded = False
    wo._active_jobs = 0
    wo._last_released = 0.0
    wo._worker_id = ""
    wo._unload_client = None
    yield
    wo._model_loaded = False
    wo._active_jobs = 0


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx():
    c = MagicMock()
    c.report_progress = MagicMock()
    c.log = MagicMock()
    return c


def _ollama_resp(text: str) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"response": text}
    r.raise_for_status = MagicMock()
    return r


def _job(service: str, payload: dict) -> dict:
    return {"id": "job-1", "service": service, "payload": payload}


# ── model manager ─────────────────────────────────────────────────────────────

def test_acquire_sets_loaded_and_increments():
    wo._acquire_model()
    assert wo._model_loaded is True
    assert wo._active_jobs == 1
    wo._release_model()


def test_acquire_increments_multiple():
    wo._acquire_model()
    wo._acquire_model()
    assert wo._active_jobs == 2
    wo._release_model()
    assert wo._active_jobs == 1
    wo._release_model()
    assert wo._active_jobs == 0


def test_release_decrements_and_sets_last_released():
    wo._active_jobs = 1
    before = time.monotonic()
    wo._release_model()
    assert wo._active_jobs == 0
    assert wo._last_released >= before


def test_execute_releases_model_on_success():
    with patch("worker_ollama.httpx.post", return_value=_ollama_resp("hello")):
        execute(_job("llm_chat", {"prompt": "hi"}), _ctx())
    assert wo._active_jobs == 0


def test_execute_releases_model_on_error():
    with patch("worker_ollama.httpx.post", side_effect=RuntimeError("ollama down")):
        with pytest.raises(RuntimeError, match="ollama down"):
            execute(_job("llm_chat", {"prompt": "hi"}), _ctx())
    assert wo._active_jobs == 0


# ── execute: translation ──────────────────────────────────────────────────────

def test_execute_translation_returns_result():
    with patch("worker_ollama.httpx.post", return_value=_ollama_resp("  Hello world  ")):
        result = execute(_job("translation", {"text": "Hola mundo", "source_lang": "es", "target_lang": "en"}), _ctx())
    assert result == {"result": "Hello world"}


def test_execute_translation_default_langs():
    """source_lang defaults to 'es', target_lang to 'en'."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _ollama_resp("Hello")

    with patch("worker_ollama.httpx.post", side_effect=fake_post):
        execute(_job("translation", {"text": "Hola"}), _ctx())

    prompt = captured["json"]["prompt"]
    assert "es" in prompt
    assert "en" in prompt
    assert "Hola" in prompt


def test_execute_translation_missing_text_raises():
    with patch("worker_ollama.httpx.post"):
        with pytest.raises(ValueError, match="payload.text"):
            execute(_job("translation", {}), _ctx())


def test_execute_translation_progress():
    ctx = _ctx()
    with patch("worker_ollama.httpx.post", return_value=_ollama_resp("ok")):
        execute(_job("translation", {"text": "Texto"}), ctx)
    calls = [c.args[0] for c in ctx.report_progress.call_args_list]
    assert calls[0] == 10
    assert calls[-1] == 100


# ── execute: llm_chat ─────────────────────────────────────────────────────────

def test_execute_llm_chat_returns_result():
    with patch("worker_ollama.httpx.post", return_value=_ollama_resp("  Paris  ")):
        result = execute(_job("llm_chat", {"prompt": "What is the capital of France?"}), _ctx())
    assert result == {"result": "Paris"}


def test_execute_llm_chat_missing_prompt_raises():
    with patch("worker_ollama.httpx.post"):
        with pytest.raises(ValueError, match="payload.prompt"):
            execute(_job("llm_chat", {}), _ctx())


def test_execute_unknown_service_raises():
    with patch("worker_ollama.httpx.post"):
        with pytest.raises(ValueError, match="unsupported service"):
            execute(_job("unknown_svc", {}), _ctx())


# ── idle watcher ──────────────────────────────────────────────────────────────

def test_idle_watcher_unloads_after_timeout():
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_client = MagicMock()
    mock_client.unload_model.return_value = mock_resp

    wo._model_loaded = True
    wo._active_jobs = 0
    wo._last_released = 0.0
    wo._unload_client = mock_client
    wo._worker_id = "w-ollama-test"

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_ollama.time") as mock_time, \
         patch("worker_ollama.httpx.post") as mock_post:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wo._IDLE_TIMEOUT + 100
        try:
            wo._idle_watcher()
        except StopIteration:
            pass

    assert wo._model_loaded is False
    mock_client.unload_model.assert_called_once_with("w-ollama-test")
    # Ollama eviction call (keep_alive=0)
    assert any(
        call.kwargs.get("json", {}).get("keep_alive") == 0
        for call in mock_post.call_args_list
    )


def test_idle_watcher_skips_unload_when_active_job():
    wo._model_loaded = True
    wo._active_jobs = 1
    wo._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_ollama.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wo._IDLE_TIMEOUT + 100
        try:
            wo._idle_watcher()
        except StopIteration:
            pass

    assert wo._model_loaded is True


def test_idle_watcher_skips_unload_before_timeout():
    wo._model_loaded = True
    wo._active_jobs = 0
    wo._last_released = 0.0

    iterations = [0]

    def fake_sleep(n):
        iterations[0] += 1
        if iterations[0] > 1:
            raise StopIteration

    with patch("worker_ollama.time") as mock_time:
        mock_time.sleep = fake_sleep
        mock_time.monotonic.return_value = wo._IDLE_TIMEOUT - 10
        try:
            wo._idle_watcher()
        except StopIteration:
            pass

    assert wo._model_loaded is True
