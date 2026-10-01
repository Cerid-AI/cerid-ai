# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""start-cerid.sh will not put the web port on the network without a sign-in
password (audit 66).

The web container adds the API key to what it forwards, so on that port the
password is the only thing between the network and the knowledge base. These
run the script's own network block under bash; nothing is reimplemented here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "start-cerid.sh"
START = "# LAN access is an explicit opt-in."
END = "# ── Tailscale trusted interface"

API_KEY = "placeholder-api-key"  # pragma: allowlist secret
PASSWORD = "placeholder-password"  # pragma: allowlist secret


def _block() -> str:
    text = SCRIPT.read_text()
    return text[text.index(START):text.index(END)]


def _run(tmp_path, env_file: str = "", **env: str) -> subprocess.CompletedProcess:
    dotenv = tmp_path / ".env"
    dotenv.write_text(env_file)
    script = tmp_path / "block.sh"
    script.write_text(_block() + '\necho "BIND=${CERID_BIND_ADDR:-unset}"\n')
    return subprocess.run(
        ["bash", str(script)],
        env={
            "PATH": os.environ["PATH"],
            "ENV_FILE": str(dotenv),
            "CERID_PORT_MCP": "8888",
            "CERID_PORT_GUI": "3000",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_lan_mode_without_a_password_refuses_to_start(tmp_path):
    proc = _run(
        tmp_path, CERID_HOST="192.168.1.50", CERID_LAN_MODE="true", CERID_API_KEY=API_KEY
    )
    assert proc.returncode == 1, proc.stdout
    assert "CERID_PORTAL_PASSWORD" in proc.stdout
    assert "BIND=" not in proc.stdout


def test_lan_mode_with_a_password_starts(tmp_path):
    proc = _run(
        tmp_path,
        CERID_HOST="192.168.1.50",
        CERID_LAN_MODE="true",
        CERID_API_KEY=API_KEY,
        CERID_PORTAL_PASSWORD=PASSWORD,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BIND=0.0.0.0" in proc.stdout


def test_the_password_is_read_from_the_env_file(tmp_path):
    proc = _run(
        tmp_path,
        env_file=(
            "CERID_LAN_MODE=true\n"
            f"CERID_API_KEY={API_KEY}\n"
            f"CERID_PORTAL_PASSWORD={PASSWORD}\n"
        ),
        CERID_HOST="192.168.1.50",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BIND=0.0.0.0" in proc.stdout


def test_lan_mode_still_refuses_without_an_api_key(tmp_path):
    proc = _run(
        tmp_path,
        CERID_HOST="192.168.1.50",
        CERID_LAN_MODE="true",
        CERID_PORTAL_PASSWORD=PASSWORD,
    )
    assert proc.returncode == 1
    assert "CERID_API_KEY" in proc.stdout


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.50"])
def test_a_network_bind_set_by_hand_needs_the_password_too(tmp_path, bind):
    proc = _run(tmp_path, CERID_HOST="localhost", CERID_BIND_ADDR=bind, CERID_API_KEY=API_KEY)
    assert proc.returncode == 1, proc.stdout
    assert "CERID_PORTAL_PASSWORD" in proc.stdout


def test_a_network_bind_set_in_the_env_file_needs_the_password_too(tmp_path):
    proc = _run(
        tmp_path,
        env_file=f"CERID_BIND_ADDR=0.0.0.0\nCERID_API_KEY={API_KEY}\n",
        CERID_HOST="localhost",
    )
    assert proc.returncode == 1, proc.stdout
    assert "CERID_PORTAL_PASSWORD" in proc.stdout


@pytest.mark.parametrize("host", ["localhost", "192.168.1.50"])
def test_a_loopback_install_without_a_password_starts_and_says_so(tmp_path, host):
    proc = _run(tmp_path, CERID_HOST=host)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARNING" in proc.stdout
    assert "CERID_PORTAL_PASSWORD" in proc.stdout
    assert "any process on this machine" in proc.stdout


def test_a_loopback_install_with_a_password_has_nothing_to_warn_about(tmp_path):
    proc = _run(tmp_path, CERID_HOST="localhost", CERID_PORTAL_PASSWORD=PASSWORD)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARNING" not in proc.stdout


def test_the_password_is_never_printed(tmp_path):
    proc = _run(
        tmp_path,
        CERID_HOST="192.168.1.50",
        CERID_LAN_MODE="true",
        CERID_API_KEY=API_KEY,
        CERID_PORTAL_PASSWORD=PASSWORD,
    )
    assert PASSWORD not in proc.stdout + proc.stderr
