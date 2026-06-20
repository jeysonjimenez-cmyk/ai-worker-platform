"""worker-ollama: translation and llm_chat worker using Ollama."""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx

from worker_base import Scaffold, from_env
from worker_base.api_client import APIClient
from worker_base.scaffold import JobContext

logger = logging.getLogger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
_IDLE_TIMEOUT = int(os.environ.get("OLLAMA_IDLE_TIMEOUT", "600"))

_TRANSLATION_PROMPT = (
    "You are a professional translator. "
    "Translate the following text from {source_lang} to {target_lang}. "
    "Output only the translated text, no explanations, no notes.\n\n{text}"
)

# ── Model manager ──────────────────────────────────────────────────────────────
# Tracks whether Ollama has the model loaded and how many jobs are active.
# Idle unload: notifies the platform ledger (releases VRAM reservation) and
# asks Ollama to evict the model from GPU memory (keep_alive=0).

_model_loaded: bool = False
_model_lock = threading.Lock()
_active_jobs: int = 0
_last_released: float = 0.0
_worker_id: str = ""
_unload_client: APIClient | None = None


def _acquire_model() -> None:
    """Mark model as in-use and increment the active-job counter."""
    global _model_loaded, _active_jobs
    with _model_lock:
        _model_loaded = True
        _active_jobs += 1


def _release_model() -> None:
    """Decrement the active-job counter and reset the idle clock."""
    global _active_jobs, _last_released
    with _model_lock:
        _active_jobs = max(0, _active_jobs - 1)
        _last_released = time.monotonic()


def _unload_ollama() -> None:
    """Ask Ollama to evict the model from VRAM (keep_alive=0)."""
    try:
        httpx.post(
            f"{_OLLAMA_HOST}/api/generate",
            json={"model": _OLLAMA_MODEL, "keep_alive": 0},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("failed to evict model from Ollama: %s", exc)


def _idle_watcher() -> None:
    """Daemon thread: unload the model after _IDLE_TIMEOUT seconds of inactivity."""
    global _model_loaded
    while True:
        time.sleep(60)
        with _model_lock:
            if not _model_loaded or _active_jobs > 0:
                continue
            if time.monotonic() - _last_released < _IDLE_TIMEOUT:
                continue
            logger.info("ollama model idle for >%ds — unloading", _IDLE_TIMEOUT)
            if _unload_client is not None and _worker_id:
                try:
                    resp = _unload_client.unload_model(_worker_id)
                    if not resp.is_success:
                        logger.warning("unload-model returned %d", resp.status_code)
                    else:
                        logger.info("unload-model notified for worker %s", _worker_id)
                except Exception as exc:
                    logger.warning("unload-model call failed: %s", exc)
            _unload_ollama()
            _model_loaded = False


# ── Ollama HTTP call ──────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    resp = httpx.post(
        f"{_OLLAMA_HOST}/api/generate",
        json={"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"]


# ── Job execution ─────────────────────────────────────────────────────────────

def execute(job: dict, ctx: JobContext) -> dict:
    service = job.get("service", "")
    payload = job.get("payload", {})

    _acquire_model()
    try:
        if service == "translation":
            source_lang = payload.get("source_lang", "es")
            target_lang = payload.get("target_lang", "en")
            text = payload.get("text", "")
            if not text:
                raise ValueError("payload.text is required for translation")
            prompt = _TRANSLATION_PROMPT.format(
                source_lang=source_lang, target_lang=target_lang, text=text
            )
            ctx.log(f"translating {len(text)} chars from {source_lang} to {target_lang}")
            ctx.report_progress(10)
            translated = _call_ollama(prompt)
            ctx.report_progress(100)
            ctx.log("translation complete")
            return {"result": translated.strip()}

        elif service == "llm_chat":
            prompt = payload.get("prompt", "")
            if not prompt:
                raise ValueError("payload.prompt is required for llm_chat")
            ctx.log(f"llm_chat: {len(prompt)} chars")
            ctx.report_progress(10)
            response = _call_ollama(prompt)
            ctx.report_progress(100)
            ctx.log("llm_chat complete")
            return {"result": response.strip()}

        else:
            raise ValueError(f"unsupported service: {service!r}")
    finally:
        _release_model()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = from_env()

    global _worker_id, _unload_client
    _worker_id = cfg.worker_id
    _unload_client = APIClient(cfg.api_url, cfg.worker_key, cfg.admin_key)

    watcher = threading.Thread(target=_idle_watcher, daemon=True, name="model-watcher")
    watcher.start()

    scaffold = Scaffold(cfg, execute=execute)
    scaffold.run()
