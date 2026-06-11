import json
import time
import urllib.request
from datetime import datetime, timezone

import pytest

from agent.collector import MetricSample
from agent.metrics_server import MetricsServer


def _sample(cpu: int = 30, ram: float = 8.0) -> MetricSample:
    return MetricSample(
        cpu_pct=cpu,
        ram_used_gb=ram,
        recorded_at=datetime.now(timezone.utc),
        gpu_util_pct=42,
        vram_total_mb=16384,
        vram_used_mb=8192,
        vram_free_mb=8192,
        temperature_c=72,
        power_w=150,
    )


def _start_server(port: int) -> MetricsServer:
    srv = MetricsServer()
    srv.start(f"127.0.0.1:{port}")
    time.sleep(0.05)  # give daemon thread time to bind
    return srv


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}


class TestMetricsServerNoSample:
    def test_503_before_first_update(self):
        _start_server(19100)
        status, body = _get("http://127.0.0.1:19100/metrics")
        assert status == 503
        assert "error" in body

    def test_404_on_unknown_path(self):
        _start_server(19101)
        status, _ = _get("http://127.0.0.1:19101/unknown")
        assert status == 404


class TestMetricsServerWithSample:
    def test_200_after_update(self):
        srv = _start_server(19102)
        srv.update(_sample())
        status, _ = _get("http://127.0.0.1:19102/metrics")
        assert status == 200

    def test_response_contains_all_fields(self):
        srv = _start_server(19103)
        srv.update(_sample(cpu=55, ram=12.5))
        _, body = _get("http://127.0.0.1:19103/metrics")
        assert body["cpu_pct"] == 55
        assert body["ram_used_gb"] == 12.5
        assert body["gpu_util_pct"] == 42
        assert body["vram_total_mb"] == 16384
        assert body["temperature_c"] == 72
        assert body["power_w"] == 150

    def test_response_includes_recorded_at(self):
        srv = _start_server(19104)
        srv.update(_sample())
        _, body = _get("http://127.0.0.1:19104/metrics")
        assert "recorded_at" in body
        assert "T" in body["recorded_at"]  # ISO 8601

    def test_update_replaces_latest(self):
        srv = _start_server(19105)
        srv.update(_sample(cpu=10))
        srv.update(_sample(cpu=99))
        _, body = _get("http://127.0.0.1:19105/metrics")
        assert body["cpu_pct"] == 99


class TestMetricsServerBind:
    def test_invalid_bind_addr_raises(self):
        srv = MetricsServer()
        with pytest.raises(ValueError, match="host:port"):
            srv.start("notavalidaddr")

    def test_missing_port_raises(self):
        srv = MetricsServer()
        with pytest.raises(ValueError):
            srv.start("127.0.0.1")
