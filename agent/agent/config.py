import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str
    worker_id: str
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
        worker_id=_require("AGENT_WORKER_ID"),
        interval_sec=int(os.environ.get("AGENT_INTERVAL_SEC", "10")),
    )
