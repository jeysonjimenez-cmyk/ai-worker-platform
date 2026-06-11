import json
from datetime import timezone
from unittest.mock import MagicMock, patch

from agent.collector import MetricSample, collect


# ── helpers ──────────────────────────────────────────────────────────────────

def _nvml_mock(gpu_util=42, mem_total_mb=16384, mem_used_mb=8192, temp=72, power_mw=150_000):
    nvml = MagicMock()
    nvml.NVMLError = Exception  # makes `except pynvml.NVMLError` catchable in tests

    util = MagicMock()
    util.gpu = gpu_util

    mem = MagicMock()
    mem.total = mem_total_mb * 1024 * 1024
    mem.used = mem_used_mb * 1024 * 1024
    mem.free = (mem_total_mb - mem_used_mb) * 1024 * 1024

    nvml.nvmlDeviceGetUtilizationRates.return_value = util
    nvml.nvmlDeviceGetMemoryInfo.return_value = mem
    nvml.nvmlDeviceGetTemperature.return_value = temp
    nvml.nvmlDeviceGetPowerUsage.return_value = power_mw
    nvml.NVML_TEMPERATURE_GPU = 0

    return nvml


def _psutil_mock(cpu_pct=30, ram_used_gb=8.0):
    mock = MagicMock()
    mock.cpu_percent.return_value = float(cpu_pct)
    vm = MagicMock()
    vm.used = int(ram_used_gb * 1024**3)
    mock.virtual_memory.return_value = vm
    return mock


# ── with GPU ─────────────────────────────────────────────────────────────────

class TestCollectWithGPU:
    def test_gpu_fields_correct(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        assert sample.gpu_util_pct == 42
        assert sample.vram_total_mb == 16384
        assert sample.vram_used_mb == 8192
        assert sample.vram_free_mb == 8192
        assert sample.temperature_c == 72
        assert sample.power_w == 150  # 150_000 mW ÷ 1000

    def test_cpu_ram_fields_correct(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock(cpu_pct=55, ram_used_gb=12.5)):
            sample = collect()

        assert sample.cpu_pct == 55
        assert sample.ram_used_gb == 12.5

    def test_field_types_are_int_or_float(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        assert type(sample.gpu_util_pct) is int
        assert type(sample.vram_total_mb) is int
        assert type(sample.vram_used_mb) is int
        assert type(sample.vram_free_mb) is int
        assert type(sample.temperature_c) is int
        assert type(sample.power_w) is int
        assert type(sample.cpu_pct) is int
        assert type(sample.ram_used_gb) is float

    def test_recorded_at_is_utc(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        assert sample.recorded_at.tzinfo == timezone.utc

    def test_to_dict_is_json_serializable(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        d = sample.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_recorded_at_is_isoformat_string(self):
        with patch("agent.collector.pynvml", _nvml_mock()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        d = sample.to_dict()
        assert isinstance(d["recorded_at"], str)
        assert "T" in d["recorded_at"]


# ── without GPU ──────────────────────────────────────────────────────────────

class TestCollectWithoutGPU:
    def _no_gpu_nvml(self):
        nvml = MagicMock()
        nvml.NVMLError = Exception
        nvml.nvmlInit.side_effect = nvml.NVMLError("no NVML driver found")
        return nvml

    def test_gpu_fields_are_none(self):
        with patch("agent.collector.pynvml", self._no_gpu_nvml()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        assert sample.gpu_util_pct is None
        assert sample.vram_total_mb is None
        assert sample.vram_used_mb is None
        assert sample.vram_free_mb is None
        assert sample.temperature_c is None
        assert sample.power_w is None

    def test_cpu_ram_still_populated(self):
        with patch("agent.collector.pynvml", self._no_gpu_nvml()), \
             patch("agent.collector.psutil", _psutil_mock(cpu_pct=25, ram_used_gb=4.0)):
            sample = collect()

        assert sample.cpu_pct == 25
        assert sample.ram_used_gb == 4.0

    def test_returns_metric_sample_instance(self):
        with patch("agent.collector.pynvml", self._no_gpu_nvml()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        assert isinstance(sample, MetricSample)

    def test_to_dict_gpu_fields_are_none(self):
        with patch("agent.collector.pynvml", self._no_gpu_nvml()), \
             patch("agent.collector.psutil", _psutil_mock()):
            sample = collect()

        d = sample.to_dict()
        assert d["gpu_util_pct"] is None
        assert d["vram_total_mb"] is None
        assert d["power_w"] is None
