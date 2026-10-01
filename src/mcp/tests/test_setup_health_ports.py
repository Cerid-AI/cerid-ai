# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/setup/health`` reports the ports the stack publishes.

The published ports are set by ``CERID_PORT_*``. The endpoint returned the
default of each as a literal, so a stack moved to other ports showed the
ports of the stack beside it.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import yaml

import app.deps as deps
import app.routers.setup as setup_router

CHROMA = "http://chroma.test:8000"
REPO_ROOT = Path(__file__).resolve().parents[3]
PORT_VARS = {
    "neo4j": ("CERID_PORT_NEO4J", 7474),
    "chromadb": ("CERID_PORT_CHROMA", 8001),
    "redis": ("CERID_PORT_REDIS", 6379),
    "mcp": ("CERID_PORT_MCP", 8888),
}


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHROMA_URL", CHROMA)
    monkeypatch.setattr(deps, "get_neo4j", lambda: MagicMock())
    monkeypatch.setattr(deps, "get_redis", lambda: MagicMock())
    monkeypatch.setattr(
        "app.agents.hallucination.startup_self_test.get_self_test_status_sync",
        lambda _redis: None,
    )
    for var, _default in PORT_VARS.values():
        monkeypatch.delenv(var, raising=False)


def _ports(body: dict) -> dict[str, int]:
    return {s["name"]: s["port"] for s in body["services"] if s["name"] in PORT_VARS}


@respx.mock
async def test_the_published_ports_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setenv("CERID_PORT_NEO4J", "7484")
    monkeypatch.setenv("CERID_PORT_CHROMA", "8011")
    monkeypatch.setenv("CERID_PORT_REDIS", "6389")
    monkeypatch.setenv("CERID_PORT_MCP", "8898")

    body = await setup_router.setup_health()

    assert _ports(body) == {"neo4j": 7484, "chromadb": 8011, "redis": 6389, "mcp": 8898}


@respx.mock
async def test_unset_ports_are_the_compose_defaults() -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(return_value=httpx.Response(200, json={}))

    body = await setup_router.setup_health()

    assert _ports(body) == {name: default for name, (_var, default) in PORT_VARS.items()}


@respx.mock
async def test_nothing_about_docker_is_made_up() -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(return_value=httpx.Response(200, json={}))

    body = await setup_router.setup_health()

    assert "compose_version" not in body["docker"]
    assert "network" not in body["docker"]


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "src/mcp/docker-compose.yml"])
def test_compose_hands_the_published_ports_to_the_api(compose_file: str) -> None:
    compose = yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))
    mcp = next(
        svc for svc in compose["services"].values()
        if any(":8888" in str(p) for p in svc.get("ports", []))
    )
    environment = [str(e) for e in mcp["environment"]]
    for var, default in PORT_VARS.values():
        assert f"{var}=${{{var}:-{default}}}" in environment, (compose_file, var)
