import json
from unittest.mock import MagicMock, patch

from agent.registration import _enumerate_gpus, register


# ── helpers ──────────────────────────────────────────────────────────────────

def _nvml_mock_with_gpu(mem_total_mb: int = 16384) -> MagicMock:
    nvml = MagicMock()
    nvml.NVMLError = Exception
    nvml.nvmlDeviceGetCount.return_value = 1

    mem = MagicMock()
    mem.total = mem_total_mb * 1024 * 1024
    nvml.nvmlDeviceGetMemoryInfo.return_value = mem

    return nvml


def _nvml_mock_no_gpu() -> MagicMock:
    nvml = MagicMock()
    nvml.NVMLError = Exception
    nvml.nvmlInit.side_effect = nvml.NVMLError("no NVML driver")
    return nvml


def _ok_urlopen():
    """Context-manager mock that simulates a 200 OK response."""
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _enumerate_gpus ───────────────────────────────────────────────────────────

class TestEnumerateGPUs:
    def test_one_gpu_returns_correct_fields(self):
        with patch("agent.registration.pynvml", _nvml_mock_with_gpu(16384)), \
             patch("agent.registration.socket.gethostname", return_value="ialab"):
            gpus = _enumerate_gpus()

        assert len(gpus) == 1
        assert gpus[0]["gpu_id"] == "ialab/gpu-0"
        assert gpus[0]["vram_total_mb"] == 16384

    def test_no_gpu_returns_empty_list(self):
        with patch("agent.registration.pynvml", _nvml_mock_no_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="ialab"):
            gpus = _enumerate_gpus()

        assert gpus == []

    def test_gpu_id_uses_real_hostname(self):
        with patch("agent.registration.pynvml", _nvml_mock_with_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="my-node"):
            gpus = _enumerate_gpus()

        assert gpus[0]["gpu_id"].startswith("my-node/")

    def test_vram_from_pynvml_not_hardcoded(self):
        with patch("agent.registration.pynvml", _nvml_mock_with_gpu(mem_total_mb=8192)), \
             patch("agent.registration.socket.gethostname", return_value="ialab"):
            gpus = _enumerate_gpus()

        assert gpus[0]["vram_total_mb"] == 8192


# ── register ─────────────────────────────────────────────────────────────────

class TestRegister:
    def test_sends_correct_payload_with_gpu(self):
        with patch("agent.registration.pynvml", _nvml_mock_with_gpu(16384)), \
             patch("agent.registration.socket.gethostname", return_value="ialab"), \
             patch("agent.registration.urllib.request.urlopen", return_value=_ok_urlopen()) as mock_open:
            register("http://vps:8080", "admin-key", "worker-key", "w-ialab")

        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data)

        assert payload["id"] == "w-ialab"
        assert payload["hostname"] == "ialab"
        assert payload["api_key"] == "worker-key"
        assert payload["gpu_id"] == "ialab/gpu-0"
        assert payload["capabilities"]["cuda"] is True
        assert payload["capabilities"]["vram_total_mb"] == 16384

    def test_sends_correct_payload_without_gpu(self):
        with patch("agent.registration.pynvml", _nvml_mock_no_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="ialab"), \
             patch("agent.registration.urllib.request.urlopen", return_value=_ok_urlopen()) as mock_open:
            register("http://vps:8080", "admin-key", "worker-key", "w-ialab")

        req = mock_open.call_args[0][0]
        payload = json.loads(req.data)

        assert "gpu_id" not in payload
        assert payload["capabilities"]["cuda"] is False
        assert payload["capabilities"]["vram_total_mb"] == 0

    def test_uses_admin_key_header(self):
        with patch("agent.registration.pynvml", _nvml_mock_no_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="ialab"), \
             patch("agent.registration.urllib.request.urlopen", return_value=_ok_urlopen()) as mock_open:
            register("http://vps:8080", "my-admin-key", "worker-key", "w-ialab")

        req = mock_open.call_args[0][0]
        # Request.get_header() normalizes key: first char upper, rest lower
        assert req.get_header("X-admin-key") == "my-admin-key"

    def test_correct_endpoint_url(self):
        with patch("agent.registration.pynvml", _nvml_mock_no_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="ialab"), \
             patch("agent.registration.urllib.request.urlopen", return_value=_ok_urlopen()) as mock_open:
            register("http://vps:8080", "admin-key", "worker-key", "w-ialab")

        req = mock_open.call_args[0][0]
        assert req.get_full_url() == "http://vps:8080/workers/register"

    def test_idempotent_second_call_does_not_raise(self):
        with patch("agent.registration.pynvml", _nvml_mock_with_gpu()), \
             patch("agent.registration.socket.gethostname", return_value="ialab"), \
             patch("agent.registration.urllib.request.urlopen", return_value=_ok_urlopen()):
            register("http://vps:8080", "admin-key", "worker-key", "w-ialab")
            register("http://vps:8080", "admin-key", "worker-key", "w-ialab")
