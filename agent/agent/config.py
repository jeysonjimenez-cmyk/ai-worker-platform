import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str       # worker key — metrics ingestion and ongoing operations
    admin_key: str     # admin key — worker registration at startup
    worker_id: str
    metrics_bind: str  # explicit host:port for local /metrics server, e.g. 100.x.x.x:9100
    interval_sec: int


def load() -> Config:
    def _require(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise RuntimeError(f"required env var {name} is not set")
        return v

    return Config(
        api_url=_require("AGENT_API_URL"),
        api_key=_require("AGENT_API_KEY"),
        admin_key=_require("AGENT_ADMIN_KEY"),
        worker_id=_require("AGENT_WORKER_ID"),
        metrics_bind=_require("AGENT_METRICS_BIND"),
        interval_sec=int(os.environ.get("AGENT_INTERVAL_SEC", "10")),
    )
