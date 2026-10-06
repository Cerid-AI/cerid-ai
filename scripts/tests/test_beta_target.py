# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The beta harness reaches the stack BETA_TARGET names, from one place.

tests/beta/lib/target.sh resolves the API URL, GUI URL, docker network and
container names for `live` (the personal stack) and `isolated` (the sandbox
overlay). tests/beta/lib/target.py is its Python twin for the conftests.
Both read the CERID_PORT_* overrides the compose file and the start scripts
read. Nothing else under tests/beta keeps a port or a container name.

# sync-manifest: allow-internal-ref — the sandbox overlay and start script are
# internal-only; the comparison against them skips where they are not shipped,
# and the rest of this file guards the public-bound tests/beta.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BETA = REPO / "tests" / "beta"
TARGET_SH = BETA / "lib" / "target.sh"

VARS = (
    "BETA_TARGET",
    "BETA_CONTAINER_SUFFIX",
    "BETA_MCP_CONTAINER",
    "BETA_NEO4J_CONTAINER",
    "BETA_CHROMA_CONTAINER",
    "BETA_REDIS_CONTAINER",
    "BETA_WEB_CONTAINER",
    "BETA_DOCKER_NETWORK",
    "BETA_MCP_PORT",
    "BETA_GUI_PORT",
    "BETA_NEO4J_PORT",
    "BETA_CHROMA_PORT",
    "BETA_REDIS_PORT",
    "BETA_MCP_URL",
    "BETA_GUI_URL",
    "BETA_CHROMA_URL",
    "BETA_NEO4J_URL",
    "BETA_MCP_BASE",
    "COMPOSE_PROJECT_NAME",
)


def _shell(env: dict[str, str]) -> dict[str, str]:
    """Source target.sh in a clean environment and read back every export."""
    printer = "; ".join(f'printf "%s=%s\\n" {v} "${{{v}:-}}"' for v in VARS)
    proc = subprocess.run(
        ["bash", "-c", f'source "$0" && {printer}', str(TARGET_SH)],
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


def _python(env: dict[str, str]) -> dict[str, str]:
    sys.path.insert(0, str(BETA))
    try:
        from lib.target import resolve_target
    finally:
        sys.path.pop(0)
    target = resolve_target(env)
    return {
        "BETA_TARGET": target.name,
        "BETA_CONTAINER_SUFFIX": target.container_suffix,
        "BETA_MCP_CONTAINER": target.mcp_container,
        "BETA_NEO4J_CONTAINER": target.neo4j_container,
        "BETA_CHROMA_CONTAINER": target.chroma_container,
        "BETA_REDIS_CONTAINER": target.redis_container,
        "BETA_WEB_CONTAINER": target.web_container,
        "BETA_DOCKER_NETWORK": target.docker_network,
        "BETA_MCP_PORT": str(target.mcp_port),
        "BETA_GUI_PORT": str(target.gui_port),
        "BETA_NEO4J_PORT": str(target.neo4j_port),
        "BETA_CHROMA_PORT": str(target.chroma_port),
        "BETA_REDIS_PORT": str(target.redis_port),
        "BETA_MCP_URL": target.mcp_url,
        "BETA_GUI_URL": target.gui_url,
        "BETA_CHROMA_URL": target.chroma_url,
        "BETA_NEO4J_URL": target.neo4j_url,
        "BETA_MCP_BASE": target.mcp_base,
        "COMPOSE_PROJECT_NAME": target.compose_project or "",
    }


def _default(script: Path, var: str) -> str:
    """The `${VAR:-default}` a start script or compose file reads for VAR."""
    match = re.search(r"\$\{" + var + r":-(\d+)\}", script.read_text())
    assert match, f"{script.name} does not read {var}"
    return match.group(1)


def test_live_is_the_default_and_names_the_personal_stack():
    got = _shell({})
    compose = REPO / "docker-compose.yml"
    assert got["BETA_TARGET"] == "live"
    assert got["BETA_CONTAINER_SUFFIX"] == ""
    assert got["BETA_MCP_CONTAINER"] == "ai-companion-mcp"
    assert got["BETA_REDIS_CONTAINER"] == "ai-companion-redis"
    assert got["BETA_WEB_CONTAINER"] == "cerid-web"
    assert got["BETA_DOCKER_NETWORK"] == "llm-network"
    assert got["BETA_MCP_PORT"] == _default(compose, "CERID_PORT_MCP")
    assert got["BETA_GUI_PORT"] == _default(compose, "CERID_PORT_GUI")
    assert got["BETA_CHROMA_PORT"] == _default(compose, "CERID_PORT_CHROMA")
    assert got["BETA_MCP_URL"] == f"http://localhost:{got['BETA_MCP_PORT']}"
    assert got["BETA_GUI_URL"] == f"http://localhost:{got['BETA_GUI_PORT']}"
    # The live project stays whatever assert.sh derives from the checkout.
    assert got["COMPOSE_PROJECT_NAME"] == ""


def test_isolated_names_the_sandbox_overlay():
    start = REPO / "scripts" / "start-sandbox.sh"
    overlay_path = REPO / "docker-compose.sandbox.yml"
    if not (start.is_file() and overlay_path.is_file()):
        pytest.skip("sandbox overlay and start script are internal-only; nothing to compare against here")
    got = _shell({"BETA_TARGET": "isolated"})
    overlay = overlay_path.read_text()
    assert got["BETA_CONTAINER_SUFFIX"] == "-sandbox"
    assert f"container_name: {got['BETA_MCP_CONTAINER']}" in overlay
    assert f"container_name: {got['BETA_REDIS_CONTAINER']}" in overlay
    assert f"container_name: {got['BETA_WEB_CONTAINER']}" in overlay
    assert f"name: {got['BETA_DOCKER_NETWORK']}" in overlay
    assert got["BETA_MCP_PORT"] == _default(start, "CERID_PORT_MCP")
    assert got["BETA_GUI_PORT"] == _default(start, "CERID_PORT_GUI")
    assert got["BETA_NEO4J_PORT"] == _default(start, "CERID_PORT_NEO4J")
    assert got["BETA_CHROMA_PORT"] == _default(start, "CERID_PORT_CHROMA")
    assert got["BETA_REDIS_PORT"] == _default(start, "CERID_PORT_REDIS")
    assert got["BETA_MCP_URL"] == f"http://localhost:{got['BETA_MCP_PORT']}"
    assert got["COMPOSE_PROJECT_NAME"] == "cerid-sandbox"


@pytest.mark.parametrize("target", ["live", "isolated"])
def test_port_overrides_are_the_ones_the_stack_scripts_read(target: str):
    got = _shell({"BETA_TARGET": target, "CERID_PORT_MCP": "18888", "CERID_PORT_GUI": "13000"})
    assert got["BETA_MCP_PORT"] == "18888"
    assert got["BETA_GUI_PORT"] == "13000"
    assert got["BETA_MCP_URL"] == "http://localhost:18888"
    assert got["BETA_GUI_URL"] == "http://localhost:13000"


@pytest.mark.parametrize("target", ["live", "isolated"])
def test_in_network_url_is_the_alias_every_stack_carries(target: str):
    # Every overlay aliases its MCP container as ai-companion-mcp on its own
    # bridge, and the container port never moves; only the host port does.
    got = _shell({"BETA_TARGET": target})
    assert got["BETA_MCP_BASE"] == "http://ai-companion-mcp:8888"


def test_an_unknown_target_is_refused():
    proc = subprocess.run(
        ["bash", "-c", 'source "$0"', str(TARGET_SH)],
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "BETA_TARGET": "staging"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "staging" in proc.stderr
    with pytest.raises(ValueError, match="staging"):
        _python({"BETA_TARGET": "staging"})


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"BETA_TARGET": "isolated"},
        {"BETA_TARGET": "live", "CERID_PORT_MCP": "18888", "CERID_PORT_REDIS": "16379"},
        {"BETA_TARGET": "isolated", "CERID_PORT_GUI": "13000"},
        {"BETA_MCP_BASE": "http://127.0.0.1:18888"},
    ],
    ids=["live", "isolated", "live-overrides", "isolated-override", "base-override"],
)
def test_python_twin_resolves_what_the_shell_does(env: dict[str, str]):
    assert _python(env) == _shell(env)


# Only the resolver may know a port or a container name; the README documents
# them for humans. Everything else must read the target, or a second stack is
# one forgotten literal away from testing the wrong one.
_ALLOWED = {
    Path("lib/target.sh"),
    Path("lib/target.py"),
    Path("README.md"),
    Path("e2e/package-lock.json"),
}
_SKIP_DIRS = {"node_modules", "reports", "__pycache__", "test-results", ".pytest_cache"}
_LITERAL = re.compile(r"(?<!\d)(8888|3000)(?!\d)|ai-companion-mcp")


def test_no_file_under_tests_beta_keeps_a_literal_port_or_container_name():
    offenders: list[str] = []
    for path in sorted(BETA.rglob("*")):
        if not path.is_file() or _SKIP_DIRS & set(path.relative_to(BETA).parts):
            continue
        rel = path.relative_to(BETA)
        if rel in _ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _LITERAL.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "literal port/container name outside lib/target.sh:\n" + "\n".join(offenders)
