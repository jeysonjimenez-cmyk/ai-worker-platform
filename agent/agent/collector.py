from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import psutil
import pynvml

logger = logging.getLogger(__name__)


@dataclass
class MetricSample:
    cpu_pct: int
    ram_used_gb: float
    recorded_at: datetime
    gpu_util_pct: int | None = None
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None
    vram_free_mb: int | None = None
    temperature_c: int | None = None
    power_w: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["recorded_at"] = self.recorded_at.isoformat()
        return d


def _read_gpu(handle) -> dict:
    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
    power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
    return {
        "gpu_util_pct": int(util.gpu),
        "vram_total_mb": int(mem.total // (1024 * 1024)),
        "vram_used_mb": int(mem.used // (1024 * 1024)),
        "vram_free_mb": int(mem.free // (1024 * 1024)),
        "temperature_c": int(temp),
        "power_w": int(power_mw // 1000),
    }


def collect(gpu_index: int = 0) -> MetricSample:
    """Collect one sample. GPU fields are None when no GPU is available."""
    now = datetime.now(timezone.utc)
    cpu = int(psutil.cpu_percent(interval=None))
    ram_used = round(psutil.virtual_memory().used / (1024**3), 2)

    gpu_fields: dict = {}
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        gpu_fields = _read_gpu(handle)
    except pynvml.NVMLError as exc:
        logger.debug("GPU metrics unavailable: %s", exc)
    finally:
        try:
            pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            pass

    return MetricSample(
        cpu_pct=cpu,
        ram_used_gb=ram_used,
        recorded_at=now,
        **gpu_fields,
    )
