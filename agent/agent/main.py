from __future__ import annotations

import logging
import sys

from agent import config, collector, registration
from agent.metrics_server import MetricsServer
from agent.reporter import Reporter, run_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def main() -> None:
    cfg = config.load()

    registration.register(cfg.api_url, cfg.admin_key, cfg.api_key, cfg.worker_id)

    srv = MetricsServer()
    srv.start(cfg.metrics_bind)

    reporter = Reporter(cfg.api_url, cfg.api_key, cfg.worker_id)

    def _collect():
        sample = collector.collect()
        srv.update(sample)
        return sample

    logger.info("agent started (interval=%ds)", cfg.interval_sec)
    run_loop(reporter, _collect, cfg.interval_sec)


if __name__ == "__main__":
    main()
