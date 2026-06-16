"""
T4.5.3 — Tests del SDK contra la API real (staging, sin mocks).

Los tests de happy path requieren credenciales de staging:
  STAGING_API_URL    — URL base de la API (ej. http://100.106.192.45:8081)
  STAGING_APP_KEY    — App key de video-crack (X-App-Key)
  STAGING_AUDIO_URL  — URL https:// público de un audio corto (~1 min)

Para el test de descarga de un job ya completado:
  STAGING_JOB_ID     — job_id de un job done con archivo VTT
  STAGING_VTT_FILE   — nombre del archivo VTT (default: output.vtt)

Sin estas variables el test se omite (no falla en CI).
El test de 503 no requiere credenciales: usa un servidor local.
"""
from __future__ import annotations

import http.server
import os
import threading
from pathlib import Path

import pytest

from ai_platform_client import Client, JobTimeoutError

_URL = os.getenv("STAGING_API_URL", "")
_KEY = os.getenv("STAGING_APP_KEY", "")
_AUDIO = os.getenv("STAGING_AUDIO_URL", "")
_JOB_ID = os.getenv("STAGING_JOB_ID", "")
_VTT = os.getenv("STAGING_VTT_FILE", "output.vtt")

_needs_staging = pytest.mark.skipif(
    not (_URL and _KEY and _AUDIO),
    reason="STAGING_API_URL, STAGING_APP_KEY, STAGING_AUDIO_URL required",
)
_needs_download = pytest.mark.skipif(
    not (_URL and _KEY and _JOB_ID),
    reason="STAGING_API_URL, STAGING_APP_KEY, STAGING_JOB_ID required",
)


@_needs_staging
def test_create_job_wait_done():
    """create_job + wait sobre un audio real → status done, resultado devuelto."""
    client = Client(_URL, _KEY)
    job_id = client.create_job(
        "transcription",
        {"audio_url": _AUDIO, "language": "es"},
    )
    assert job_id, "create_job debe devolver un job_id no vacío"

    job = client.wait(job_id, timeout=600)
    assert job["status"] == "done", (
        f"se esperaba done, got {job['status']}: {job.get('error_msg')}"
    )
    assert job["id"] == job_id


@_needs_download
def test_download_retrieves_vtt(tmp_path: Path):
    """download() recupera la VTT de un job completado y escribe a disco."""
    client = Client(_URL, _KEY)
    dest = tmp_path / _VTT
    client.download(_JOB_ID, _VTT, dest)
    assert dest.exists(), "el archivo de destino debe existir tras download"
    assert dest.stat().st_size > 0, "el archivo no debe estar vacío"
    assert "WEBVTT" in dest.read_text(errors="replace"), "debe ser un VTT válido"


@_needs_staging
def test_wait_timeout_raises():
    """wait() lanza JobTimeoutError cuando el timeout expira antes de que el job termine."""
    client = Client(_URL, _KEY)
    job_id = client.create_job(
        "transcription",
        {"audio_url": _AUDIO, "language": "es"},
    )
    with pytest.raises(JobTimeoutError, match="did not complete"):
        client.wait(job_id, timeout=0.1)


def test_download_503_raises(tmp_path: Path):
    """download() lanza RuntimeError con mensaje claro cuando la API devuelve 503."""

    class _503Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.end_headers()

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _503Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    try:
        client = Client(f"http://127.0.0.1:{port}", "test-key")
        with pytest.raises(RuntimeError, match="503"):
            client.download("job-123", "output.vtt", tmp_path / "out.vtt")
    finally:
        srv.server_close()
        t.join(timeout=5)
