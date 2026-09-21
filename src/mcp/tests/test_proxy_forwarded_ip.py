# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The GUI proxy must forward the client's address.

Browsers reach the API through the same-origin `/api/mcp/` proxy in the
cerid-web container (VITE_MCP_URL=/api/mcp is the LAN/gateway default). If
nginx does not forward the peer address, every browser client arrives as the
container IP: one rate-limit bucket for the whole LAN, and one redacted hash
in every unauthorized-request log line.
"""
from __future__ import annotations

import ipaddress
import re
from types import SimpleNamespace

import pytest

from tests._helpers import repo_root


def _api_proxy_block() -> str:
    root = repo_root()
    if root is None:
        pytest.skip("no repo checkout (shallow container mount)")
    conf = root / "src" / "web" / "nginx.conf"
    if not conf.exists():
        pytest.skip("nginx.conf not present in this tree")
    match = re.search(
        r"location /api/mcp/ \{(.*?)\n    \}", conf.read_text(), re.DOTALL,
    )
    assert match, "the /api/mcp/ proxy block moved or changed shape"
    return match.group(1)


def test_proxy_forwards_the_client_address():
    block = _api_proxy_block()
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block
    assert "proxy_set_header X-Real-IP $remote_addr;" in block


def _request(peer: str, forwarded: str = ""):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_the_header_is_what_separates_clients(monkeypatch):
    """Why the nginx line matters: the header is the only input.

    With TRUSTED_PROXIES configured but no forwarded header, every browser
    collapses onto the proxy's own address — the state the rate limiter and
    the auth log were in.
    """
    from app.middleware import rate_limit

    monkeypatch.setattr(
        rate_limit, "TRUSTED_PROXIES", [ipaddress.ip_network("172.17.0.0/16")],
    )
    assert rate_limit.get_client_ip(
        _request("172.17.0.5", "192.168.1.20"),
    ) == "192.168.1.20"
    assert rate_limit.get_client_ip(_request("172.17.0.5")) == "172.17.0.5"
