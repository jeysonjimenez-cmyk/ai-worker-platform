"""Validate static deploy artifacts: systemd unit file and install script."""
from __future__ import annotations

import configparser
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
UNIT_FILE = REPO_ROOT / "deploy" / "ialab" / "node-agent.service"
INSTALL_SCRIPT = REPO_ROOT / "deploy" / "ialab" / "install-agent.sh"
ENV_EXAMPLE = REPO_ROOT / "deploy" / "ialab" / "agent.env.example"


def _parse_unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False)
    parser.read_string(UNIT_FILE.read_text())
    return parser


class TestUnitFileExists:
    def test_unit_file_present(self):
        assert UNIT_FILE.exists(), f"missing: {UNIT_FILE}"

    def test_install_script_present(self):
        assert INSTALL_SCRIPT.exists(), f"missing: {INSTALL_SCRIPT}"

    def test_env_example_present(self):
        assert ENV_EXAMPLE.exists(), f"missing: {ENV_EXAMPLE}"


class TestUnitFileRequiredDirectives:
    def test_restart_always(self):
        cfg = _parse_unit()
        assert cfg.get("Service", "Restart") == "always"

    def test_after_network_online(self):
        cfg = _parse_unit()
        after = cfg.get("Unit", "After")
        assert "network-online.target" in after

    def test_wants_network_online(self):
        cfg = _parse_unit()
        wants = cfg.get("Unit", "Wants")
        assert "network-online.target" in wants

    def test_environment_file_set(self):
        cfg = _parse_unit()
        env_file = cfg.get("Service", "EnvironmentFile")
        assert env_file.startswith("/etc/"), f"EnvironmentFile must be under /etc/, got: {env_file}"

    def test_exec_start_uses_venv_python(self):
        cfg = _parse_unit()
        exec_start = cfg.get("Service", "ExecStart")
        assert ".venv" in exec_start
        assert "agent.main" in exec_start

    def test_syslog_identifier_set(self):
        cfg = _parse_unit()
        assert cfg.get("Service", "SyslogIdentifier") == "node-agent"

    def test_wanted_by_multi_user(self):
        cfg = _parse_unit()
        assert "multi-user.target" in cfg.get("Install", "WantedBy")

    def test_user_placeholder_present(self):
        # The template must contain __AGENT_USER__ so install-agent.sh can substitute it.
        raw = UNIT_FILE.read_text()
        assert "__AGENT_USER__" in raw, "unit file must contain __AGENT_USER__ placeholder"

    def test_restart_sec_set(self):
        cfg = _parse_unit()
        # RestartSec must be a positive integer
        val = int(cfg.get("Service", "RestartSec"))
        assert val > 0


class TestInstallScript:
    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed: {result.stderr}"

    def test_script_is_executable(self):
        mode = os.stat(INSTALL_SCRIPT).st_mode
        assert mode & 0o111, "install-agent.sh must be executable"

    def test_script_substitutes_agent_user(self):
        raw = INSTALL_SCRIPT.read_text()
        assert "__AGENT_USER__" in raw or "AGENT_USER" in raw

    def test_script_references_uv_sync(self):
        raw = INSTALL_SCRIPT.read_text()
        assert "uv sync" in raw

    def test_script_does_not_overwrite_env_file(self):
        raw = INSTALL_SCRIPT.read_text()
        # The script must check for existence before copying the env template.
        assert "! -f" in raw or "if [ ! -f" in raw

    def test_script_sets_env_file_chmod_600(self):
        raw = INSTALL_SCRIPT.read_text()
        assert "600" in raw


class TestEnvExample:
    def test_contains_required_keys(self):
        raw = ENV_EXAMPLE.read_text()
        for key in ("AGENT_API_URL", "AGENT_API_KEY", "AGENT_ADMIN_KEY",
                    "AGENT_WORKER_ID", "AGENT_METRICS_BIND"):
            assert key in raw, f"missing {key} in agent.env.example"

    def test_no_real_secrets(self):
        raw = ENV_EXAMPLE.read_text()
        # All secret-like values must be placeholders
        assert "change_me" in raw
