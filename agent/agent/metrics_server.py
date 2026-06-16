from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from agent.collector import MetricSample


class MetricsServer:
    """Serves the latest MetricSample at GET /metrics on an explicit bind address.

    Thread-safe: update() can be called from any thread while the server runs.
    The bind address must be set explicitly (AGENT_METRICS_BIND) — never 0.0.0.0.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[MetricSample] = None

    def update(self, sample: MetricSample) -> None:
        with self._lock:
            self._latest = sample

    def start(self, bind_addr: str) -> None:
        """Start serving in a daemon thread. Raises ValueError if bind_addr is not host:port."""
        host, _, port_str = bind_addr.rpartition(":")
        if not host or not port_str:
            raise ValueError(f"AGENT_METRICS_BIND must be 'host:port', got: {bind_addr!r}")

        server = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/metrics":
                    self.send_response(404)
                    self.end_headers()
                    return
                with server._lock:
                    sample = server._latest
                if sample is None:
                    body = b'{"error":"no sample collected yet"}'
                    self.send_response(503)
                else:
                    body = json.dumps(sample.to_dict()).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                pass  # suppress default per-request logging

        httpd = HTTPServer((host, int(port_str)), _Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
