from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_url: str
    worker_key: str
    admin_key: str
    worker_id: str
    hostname: str
    capabilities: dict
    heartbeat_interval: int  # seconds between heartbeats
    poll_wait: int           # long-poll timeout sent to /claim?wait=N
    backoff_max: int         # max backoff seconds between claim retries


def from_env() -> Config:
    def _require(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise RuntimeError(f"required env var {name!r} is not set")
        return v

    caps_raw = os.environ.get("WORKER_CAPABILITIES", "{}")
    try:
        capabilities = json.loads(caps_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"WORKER_CAPABILITIES must be valid JSON: {exc}") from exc

    return Config(
        api_url=_require("WORKER_API_URL").rstrip("/"),
        worker_key=_require("WORKER_KEY"),
        admin_key=_require("WORKER_ADMIN_KEY"),
        worker_id=_require("WORKER_ID"),
        hostname=os.environ.get("HOSTNAME", "").strip() or socket.gethostname(),
        capabilities=capabilities,
        heartbeat_interval=int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "10")),
        poll_wait=int(os.environ.get("WORKER_POLL_WAIT", "30")),
        backoff_max=int(os.environ.get("WORKER_BACKOFF_MAX", "30")),
    )
