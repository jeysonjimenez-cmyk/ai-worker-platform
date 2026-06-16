"""worker-whisper: transcription worker using faster-whisper (CUDA)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from worker_base import Scaffold, from_env
from worker_base.api_client import APIClient
from worker_base.scaffold import JobContext
from .downloader import download, DEFAULT_MAX_BYTES, SSRFError, FileTooLargeError

logger = logging.getLogger(__name__)

_FILES_DIR = os.environ.get("WORKER_FILES_DIR", "/app/files")
_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v2")
_WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
_WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
_IDLE_TIMEOUT = int(os.environ.get("WHISPER_IDLE_TIMEOUT", "600"))
_AUDIO_MAX_BYTES = int(os.environ.get("AUDIO_MAX_SIZE_BYTES", str(DEFAULT_MAX_BYTES)))

# ── Model manager ──────────────────────────────────────────────────────────────
# The model is loaded lazily (first job) and unloaded after _IDLE_TIMEOUT seconds
# of inactivity by the background watcher thread. The lock is held during the
# unload API call to prevent a TOCTOU race where a new job loads the model before
# the unload notification reaches the ledger.

_model = None
_model_lock = threading.Lock()
_active_jobs: int = 0
_last_released: float = 0.0
_worker_id: str = ""
_unload_client: APIClient | None = None


def _load_model():
    from faster_whisper import WhisperModel
    logger.info("loading whisper model %s on %s", _WHISPER_MODEL, _WHISPER_DEVICE)
    return WhisperModel(_WHISPER_MODEL, device=_WHISPER_DEVICE, compute_type=_WHISPER_COMPUTE)


def _acquire_model():
    """Return the cached model (loading it if needed) and increment the active-job counter."""
    global _model, _active_jobs
    with _model_lock:
        if _model is None:
            _model = _load_model()
        _active_jobs += 1
        return _model


def _release_model() -> None:
    """Decrement the active-job counter and reset the idle clock."""
    global _active_jobs, _last_released
    with _model_lock:
        _active_jobs = max(0, _active_jobs - 1)
        _last_released = time.monotonic()


def _idle_watcher() -> None:
    """Daemon thread: unload the model after _IDLE_TIMEOUT seconds of inactivity."""
    global _model
    while True:
        time.sleep(60)
        with _model_lock:
            if _model is None or _active_jobs > 0:
                continue
            if time.monotonic() - _last_released < _IDLE_TIMEOUT:
                continue
            logger.info("whisper model idle for >%ds — unloading", _IDLE_TIMEOUT)
            if _unload_client is not None and _worker_id:
                try:
                    resp = _unload_client.unload_model(_worker_id)
                    if not resp.is_success:
                        logger.warning("unload-model returned %d", resp.status_code)
                    else:
                        logger.info("unload-model notified for worker %s", _worker_id)
                except Exception as exc:
                    logger.warning("unload-model call failed: %s", exc)
            _model = None


# ── Output writers ────────────────────────────────────────────────────────────

def _fmt_ts(seconds: float, sep: str = ".") -> str:
    """Format seconds → HH:MM:SS{sep}mmm (VTT uses '.', SRT uses ',')."""
    ms = round(seconds * 1000)
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _write_vtt(segments: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in segments:
            f.write(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")


def _write_srt(segments: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{_fmt_ts(seg.start, ',')} --> {_fmt_ts(seg.end, ',')}\n")
            f.write(f"{seg.text.strip()}\n\n")


def _write_json(segments: list, info, path: Path) -> None:
    data = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": [
            {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
            for seg in segments
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Job execution ─────────────────────────────────────────────────────────────

def execute(job: dict, ctx: JobContext) -> dict:
    payload = job.get("payload", {})
    audio_url = payload.get("audio_url", "")
    job_id = job["id"]

    files_dir = Path(_FILES_DIR) / job_id
    files_dir.mkdir(parents=True, exist_ok=True)

    # Download audio (SSRF + size validated by downloader)
    audio_path = files_dir / "audio"
    ctx.log(f"downloading audio from {audio_url!r}")
    try:
        download(audio_url, audio_path, max_bytes=_AUDIO_MAX_BYTES)
    except SSRFError as exc:
        raise ValueError(f"audio_url blocked by SSRF policy: {exc}") from exc
    except FileTooLargeError as exc:
        raise ValueError(f"audio file too large: {exc}") from exc

    ctx.report_progress(5)
    ctx.log("audio downloaded, starting transcription")

    model = _acquire_model()
    try:
        segments_gen, info = model.transcribe(str(audio_path), beam_size=5)

        all_segments = []
        total_duration = info.duration or 1.0
        for seg in segments_gen:
            all_segments.append(seg)
            pct = min(95, int(5 + (seg.end / total_duration) * 88))
            ctx.report_progress(pct)
    finally:
        _release_model()

    # Write output files
    vtt_path = files_dir / "output.vtt"
    srt_path = files_dir / "output.srt"
    json_path = files_dir / "output.json"

    _write_vtt(all_segments, vtt_path)
    _write_srt(all_segments, srt_path)
    _write_json(all_segments, info, json_path)

    # Register each file in job_files via the API
    for fname, fpath in [
        ("output.vtt", vtt_path),
        ("output.srt", srt_path),
        ("output.json", json_path),
    ]:
        ctx.upload_file_metadata(fname, f"files/{job_id}/{fname}", size_bytes=fpath.stat().st_size)

    ctx.report_progress(100)
    ctx.log(f"transcription complete: {len(all_segments)} segments, language={info.language}")

    audio_path.unlink(missing_ok=True)

    return {
        "language": info.language,
        "duration": info.duration,
        "segments": len(all_segments),
        "files": ["output.vtt", "output.srt", "output.json"],
    }


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
