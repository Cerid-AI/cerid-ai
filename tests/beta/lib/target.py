# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Python twin of tests/beta/lib/target.sh for the conftests.

Same inputs (BETA_TARGET, the CERID_PORT_* overrides, BETA_MCP_BASE), same
values; scripts/tests/test_beta_target.py holds the two files to each other.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_PORT_DEFAULTS: dict[str, dict[str, int]] = {
    "live": {"MCP": 8888, "GUI": 3000, "NEO4J": 7474, "CHROMA": 8001, "REDIS": 6379},
    "isolated": {"MCP": 8898, "GUI": 3010, "NEO4J": 7484, "CHROMA": 8011, "REDIS": 6389},
}
_SUFFIX = {"live": "", "isolated": "-sandbox"}
_NETWORK = {"live": "llm-network", "isolated": "cerid-sandbox-llm-network"}


@dataclass(frozen=True)
class BetaTarget:
    name: str
    container_suffix: str
    docker_network: str
    compose_project: str | None
    mcp_port: int
    gui_port: int
    neo4j_port: int
    chroma_port: int
    redis_port: int
    mcp_base: str

    @property
    def mcp_container(self) -> str:
        return f"ai-companion-mcp{self.container_suffix}"

    @property
    def neo4j_container(self) -> str:
        return f"ai-companion-neo4j{self.container_suffix}"

    @property
    def chroma_container(self) -> str:
        return f"ai-companion-chroma{self.container_suffix}"

    @property
    def redis_container(self) -> str:
        return f"ai-companion-redis{self.container_suffix}"

    @property
    def web_container(self) -> str:
        return f"cerid-web{self.container_suffix}"

    @property
    def mcp_url(self) -> str:
        return f"http://localhost:{self.mcp_port}"

    @property
    def gui_url(self) -> str:
        return f"http://localhost:{self.gui_port}"

    @property
    def chroma_url(self) -> str:
        return f"http://localhost:{self.chroma_port}"

    @property
    def neo4j_url(self) -> str:
        return f"http://localhost:{self.neo4j_port}"


def resolve_target(env: Mapping[str, str] | None = None) -> BetaTarget:
    env = os.environ if env is None else env
    name = env.get("BETA_TARGET") or "live"
    if name not in _PORT_DEFAULTS:
        raise ValueError(f"unknown BETA_TARGET '{name}' (expected live or isolated)")

    def port(service: str) -> int:
        return int(env.get(f"CERID_PORT_{service}") or _PORT_DEFAULTS[name][service])

    compose_project = None
    if name == "isolated":
        compose_project = env.get("COMPOSE_PROJECT_NAME") or env.get("CERID_SANDBOX_PROJECT") or "cerid-sandbox"
    return BetaTarget(
        name=name,
        container_suffix=_SUFFIX[name],
        docker_network=_NETWORK[name],
        compose_project=compose_project,
        mcp_port=port("MCP"),
        gui_port=port("GUI"),
        neo4j_port=port("NEO4J"),
        chroma_port=port("CHROMA"),
        redis_port=port("REDIS"),
        mcp_base=env.get("BETA_MCP_BASE") or "http://ai-companion-mcp:8888",
    )
