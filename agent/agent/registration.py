from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request

import pynvml

logger = logging.getLogger(__name__)


def _enumerate_gpus() -> list[dict]:
    """Return [{gpu_id, vram_total_mb}, ...] for each GPU found via pynvml."""
    hostname = socket.gethostname()
    gpus: list[dict] = []
    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append({
                "gpu_id": f"{hostname}/gpu-{i}",
                "vram_total_mb": int(mem.total // (1024 * 1024)),
            })
    except pynvml.NVMLError as exc:
        logger.info("No GPU found during registration: %s", exc)
    finally:
        try:
            pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            pass
    return gpus


def register(api_url: str, admin_key: str, api_key: str, worker_id: str) -> None:
    """Register this node with the API. Idempotent — safe to call on every restart.

    Uses admin_key for the registration endpoint (X-Admin-Key).
    api_key is stored as the worker's ongoing credential.
    """
    hostname = socket.gethostname()
    gpus = _enumerate_gpus()

    gpu_id = gpus[0]["gpu_id"] if gpus else None
    vram_total_mb = gpus[0]["vram_total_mb"] if gpus else 0

    payload: dict = {
        "id": worker_id,
        "hostname": hostname,
        "api_key": api_key,
        "capabilities": {
            "services": [],
            "cuda": len(gpus) > 0,
            "vram_total_mb": vram_total_mb,
        },
    }
    if gpu_id:
        payload["gpu_id"] = gpu_id

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{api_url}/workers/register",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": admin_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        logger.info(
            "registered worker %s (gpu_id=%s, vram=%d MB, status=%d)",
            worker_id, gpu_id, vram_total_mb, resp.status,
        )
