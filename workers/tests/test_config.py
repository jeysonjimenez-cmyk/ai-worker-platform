import pytest

from worker_base.config import from_env


def _set_required(monkeypatch, **overrides):
    defaults = {
        "WORKER_API_URL": "http://vps:8080",
        "WORKER_KEY": "wk",
        "WORKER_ADMIN_KEY": "ak",
        "WORKER_ID": "w-1",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_missing_api_url(monkeypatch):
    _set_required(monkeypatch, WORKER_API_URL=None)
    with pytest.raises(RuntimeError, match="WORKER_API_URL"):
        from_env()


def test_missing_worker_key(monkeypatch):
    _set_required(monkeypatch, WORKER_KEY=None)
    with pytest.raises(RuntimeError, match="WORKER_KEY"):
        from_env()


def test_missing_admin_key(monkeypatch):
    _set_required(monkeypatch, WORKER_ADMIN_KEY=None)
    with pytest.raises(RuntimeError, match="WORKER_ADMIN_KEY"):
        from_env()


def test_missing_worker_id(monkeypatch):
    _set_required(monkeypatch, WORKER_ID=None)
    with pytest.raises(RuntimeError, match="WORKER_ID"):
        from_env()


def test_invalid_capabilities_json(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("WORKER_CAPABILITIES", "{not json}")
    with pytest.raises(ValueError, match="WORKER_CAPABILITIES"):
        from_env()


def test_valid_config(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("WORKER_CAPABILITIES", '{"services":["echo"]}')
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL", "15")
    cfg = from_env()
    assert cfg.api_url == "http://vps:8080"
    assert cfg.worker_key == "wk"
    assert cfg.admin_key == "ak"
    assert cfg.worker_id == "w-1"
    assert cfg.capabilities == {"services": ["echo"]}
    assert cfg.heartbeat_interval == 15


def test_trailing_slash_stripped(monkeypatch):
    _set_required(monkeypatch, WORKER_API_URL="http://vps:8080/")
    cfg = from_env()
    assert cfg.api_url == "http://vps:8080"
