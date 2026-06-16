"""worker-echo: scaffold demonstration worker that echoes the job payload."""
import logging

from worker_base import Scaffold, from_env
from worker_base.scaffold import JobContext

logger = logging.getLogger(__name__)


def execute(job: dict, ctx: JobContext) -> dict:
    ctx.log(f"echo worker: processing job {job['id']}")
    ctx.report_progress(50)
    result = {"echo": job.get("payload", {})}
    ctx.log("echo worker: done")
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = from_env()
    scaffold = Scaffold(cfg, execute=execute)
    scaffold.run()
