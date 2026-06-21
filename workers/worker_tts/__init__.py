"""worker-tts: TTS worker using Higgs Audio v3 (Docker+SGLang-Omni sidecar)."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx

from worker_base import Scaffold, from_env
from worker_base.api_client import APIClient
from worker_base.scaffold import JobContext

logger = logging.getLogger(__name__)

_TTS_HOST = os.environ.get("TTS_HOST", "http://127.0.0.1:8002")
_FILES_DIR = os.environ.get("WORKER_FILES_DIR", "/app/files")
_IDLE_TIMEOUT = int(os.environ.get("TTS_IDLE_TIMEOUT", "600"))
# Name of the Higgs sidecar container — used by docker stop/start for idle unload/reload.
# Must match the container name in docker-compose (service: higgs-tts → ialab-higgs-tts-1).
_HIGGS_CONTAINER = os.environ.get("HIGGS_CONTAINER", "ialab-higgs-tts-1")
# Static voice-cloning reference (optional): path inside the Higgs container, pre-mounted via compose.
_REFERENCE_AUDIO_PATH = os.environ.get("TTS_REFERENCE_AUDIO_PATH", "")
_REFERENCE_TEXT = os.environ.get("TTS_REFERENCE_TEXT", "")

# ── Model manager ──────────────────────────────────────────────────────────────
# SGLang-Omni loads the model at container startup and has no API endpoint to
# release GPU memory (verified: /flush_cache → 404, /v1/clear → 404). The only
# way to free the 14720 MiB is to stop the higgs-tts container. The worker-tts
# container mounts /var/run/docker.sock to do this via `docker stop/start`.
#
# _model_loaded starts True (higgs is up at deploy time). Idle unload:
#   1. notify the platform ledger (ReleaseModelVRAM via unload-model)
#   2. docker stop _HIGGS_CONTAINER  → VRAM freed for whisper/ollama
# On next TTS job: docker start _HIGGS_CONTAINER + wait for /health → ready.

_model_loaded: bool = True   # Higgs model is in VRAM from container startup
_model_lock = threading.Lock()
_active_jobs: int = 0
_last_released: float = time.monotonic()  # idle clock starts at startup
_worker_id: str = ""
_unload_client: APIClient | None = None


def _acquire_model() -> None:
    """Mark model as in-use and increment the active-job counter."""
    global _model_loaded, _active_jobs
    with _model_lock:
        _model_loaded = True
        _active_jobs += 1


def _release_model() -> None:
    """Decrement the active-job counter. If no more active jobs, unload immediately.

    TTS occupies 14720 MiB — nearly the full GPU. Waiting for idle timeout would
    leave a window where the ledger shows 0 reserved (job done) but higgs still
    holds 14720 MiB in VRAM, allowing whisper/ollama to be claimed and OOM-killed.
    Unloading immediately after each job closes that window.
    """
    global _active_jobs, _last_released, _model_loaded
    with _model_lock:
        _active_jobs = max(0, _active_jobs - 1)
        _last_released = time.monotonic()
        if _active_jobs == 0 and _model_loaded:
            # Unload immediately: notify ledger + stop higgs container.
            # The idle watcher checks _model_loaded=False and skips further action.
            logger.info("tts job completed — unloading higgs immediately")
            if _unload_client is not None and _worker_id:
                try:
                    resp = _unload_client.unload_model(_worker_id)
                    if not resp.is_success:
                        logger.warning("unload-model returned %d", resp.status_code)
                    else:
                        logger.info("unload-model notified for worker %s", _worker_id)
                except Exception as exc:
                    logger.warning("unload-model call failed: %s", exc)
            _unload_higgs()
            _model_loaded = False


def _unload_higgs() -> None:
    """Stop the Higgs container to release its 14720 MiB from GPU memory."""
    try:
        subprocess.run(["docker", "stop", _HIGGS_CONTAINER], check=True, capture_output=True, timeout=30)
        logger.info("higgs container %s stopped — VRAM freed", _HIGGS_CONTAINER)
    except Exception as exc:
        logger.warning("could not stop higgs container %s: %s", _HIGGS_CONTAINER, exc)


def _ensure_higgs_ready() -> None:
    """Start the Higgs container if stopped and wait for /health to return 200."""
    try:
        subprocess.run(["docker", "start", _HIGGS_CONTAINER], check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError:
        pass  # already running is fine
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{_TTS_HOST}/health", timeout=5)
            if r.is_success:
                logger.info("higgs ready on %s", _TTS_HOST)
                return
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError(f"higgs container {_HIGGS_CONTAINER} did not become ready in 300s")


def _idle_watcher() -> None:
    """Daemon thread: safety net for the startup case and any missed _release_model calls.

    Normal path: _release_model() unloads immediately after each job.
    This watcher handles the startup load (higgs starts with the model in VRAM)
    and any edge case where _release_model was skipped (e.g. unhandled exception).
    """
    global _model_loaded
    while True:
        time.sleep(60)
        with _model_lock:
            if not _model_loaded or _active_jobs > 0:
                continue
            if time.monotonic() - _last_released < _IDLE_TIMEOUT:
                continue
            logger.info("tts model idle for >%ds — unloading (safety-net)", _IDLE_TIMEOUT)
            if _unload_client is not None and _worker_id:
                try:
                    resp = _unload_client.unload_model(_worker_id)
                    if not resp.is_success:
                        logger.warning("unload-model returned %d", resp.status_code)
                    else:
                        logger.info("unload-model notified for worker %s", _worker_id)
                except Exception as exc:
                    logger.warning("unload-model call failed: %s", exc)
            _unload_higgs()
            _model_loaded = False


# ── Higgs HTTP call ───────────────────────────────────────────────────────────

def _synthesize(text: str) -> bytes:
    body: dict = {
        "model": "higgs-audio-1",
        "input": text,
        "response_format": "mp3",
    }
    if _REFERENCE_AUDIO_PATH:
        body["references"] = [{"audio_path": _REFERENCE_AUDIO_PATH, "text": _REFERENCE_TEXT}]

    resp = httpx.post(f"{_TTS_HOST}/v1/audio/speech", json=body, timeout=300)
    resp.raise_for_status()
    return resp.content


# ── Job execution ─────────────────────────────────────────────────────────────

def execute(job: dict, ctx: JobContext) -> dict:
    payload = job.get("payload", {})
    text = payload.get("text", "")
    job_id = job["id"]

    if not text:
        raise ValueError("payload.text is required for tts")

    files_dir = Path(_FILES_DIR) / job_id
    files_dir.mkdir(parents=True, exist_ok=True)

    ctx.log(f"synthesizing {len(text)} chars")
    ctx.report_progress(10)

    _acquire_model()
    try:
        _ensure_higgs_ready()
        audio_bytes = _synthesize(text)
        ctx.report_progress(90)
    finally:
        _release_model()

    audio_path = files_dir / "output.mp3"
    audio_path.write_bytes(audio_bytes)

    ctx.upload_file_metadata("output.mp3", f"files/{job_id}/output.mp3", size_bytes=audio_path.stat().st_size)
    ctx.report_progress(100)
    ctx.log(f"tts complete: {audio_path.stat().st_size} bytes")

    return {"files": ["output.mp3"]}


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
