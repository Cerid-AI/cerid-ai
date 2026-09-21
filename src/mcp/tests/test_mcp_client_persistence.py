# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""External MCP server registrations must survive a restart (F060).

``add_server()`` only wrote to an in-process dict and nothing in the app
lifespan ever called ``load_config()``, so every server a user registered
through Settings -> Extensions vanished on the next container restart —
``GET /mcp-servers`` empty, its ``ext_*`` tools gone, and no reconnect path
because the config itself was lost.
"""

from __future__ import annotations

import json

import pytest


class FakeRedis:
    """Just the hash operations the manager uses."""

    def __init__(self) -> None:
        self.store: dict[str, dict[bytes, bytes]] = {}

    def hset(self, key: str, field: str, value: str) -> None:
        self.store.setdefault(key, {})[field.encode()] = value.encode()

    def hdel(self, key: str, field: str) -> None:
        self.store.get(key, {}).pop(field.encode(), None)

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        return dict(self.store.get(key, {}))


@pytest.fixture
def redis(monkeypatch):
    import utils.mcp_client as mcp_client_mod

    fake = FakeRedis()
    monkeypatch.setattr(mcp_client_mod, "_store", lambda: fake, raising=False)
    return fake


def _cfg(name="notes"):
    from utils.mcp_client import MCPServerConfig

    return MCPServerConfig(
        name=name,
        transport="sse",
        url="https://notes.example/sse",
        headers={"Authorization": "Bearer t"},
    )


def test_add_server_writes_through_to_the_store(redis):
    from utils.mcp_client import MCPClientManager

    MCPClientManager().add_server(_cfg())

    saved = redis.hgetall("cerid:mcp_servers")
    assert list(saved) == [b"notes"]
    assert json.loads(saved[b"notes"])["url"] == "https://notes.example/sse"


def test_a_fresh_process_hydrates_what_the_previous_one_registered(redis):
    from utils.mcp_client import MCPClientManager

    MCPClientManager().add_server(_cfg())

    restarted = MCPClientManager()
    assert restarted.list_servers() == []

    assert restarted.hydrate_from_store() == 1
    names = [s["name"] for s in restarted.list_servers()]
    assert names == ["notes"]
    # Round-trip must preserve the fields needed to reconnect, not just the
    # summary list_servers() publishes.
    cfg = restarted._configs["notes"]
    assert cfg.transport == "sse"
    assert cfg.url == "https://notes.example/sse"
    assert cfg.headers == {"Authorization": "Bearer t"}


def test_remove_server_clears_the_store(redis):
    from utils.mcp_client import MCPClientManager

    mgr = MCPClientManager()
    mgr.add_server(_cfg())
    assert mgr.remove_server("notes") is True

    assert redis.hgetall("cerid:mcp_servers") == {}
    assert MCPClientManager().hydrate_from_store() == 0


@pytest.mark.asyncio
async def test_connect_all_skips_servers_already_connected(redis):
    """routers/mcp_client.py calls connect_all() on every add.

    Re-entering _connect_one for an already-connected server overwrote
    self._sessions[name] and leaked the previous stdio subprocess into the
    exit stack.
    """
    from utils.mcp_client import MCPClientManager

    mgr = MCPClientManager()
    mgr.add_server(_cfg("a"))
    mgr.add_server(_cfg("b"))

    attempts: list[str] = []

    async def fake_connect_one(cfg):
        attempts.append(cfg.name)

        class _S:
            async def list_tools(self):
                class _R:
                    tools: list = []
                return _R()

        return _S()

    mgr._connect_one = fake_connect_one  # type: ignore[method-assign]

    assert sorted(await mgr.connect_all()) == ["a", "b"]
    assert sorted(attempts) == ["a", "b"]

    await mgr.connect_all()
    assert sorted(attempts) == ["a", "b"], f"reconnected: {attempts}"


@pytest.mark.asyncio
async def test_restore_and_connect_reads_env_config_and_the_store(redis, monkeypatch):
    """Both durable sources feed the startup restore."""
    import utils.mcp_client as mcp_client_mod

    monkeypatch.setenv(
        "MCP_SERVERS_CONFIG",
        json.dumps([{"name": "from-env", "transport": "sse", "url": "https://e/sse"}]),
    )
    mcp_client_mod.MCPClientManager().add_server(_cfg("from-store"))

    mgr = mcp_client_mod.MCPClientManager()
    monkeypatch.setattr(mcp_client_mod, "mcp_client_manager", mgr)

    async def fake_connect_one(cfg):
        class _S:
            async def list_tools(self):
                class _R:
                    tools: list = []
                return _R()

        return _S()

    mgr._connect_one = fake_connect_one  # type: ignore[method-assign]

    assert sorted(await mcp_client_mod.restore_and_connect()) == [
        "from-env",
        "from-store",
    ]


def test_the_lifespan_performs_the_restore():
    """Nothing in the lifespan called this, which is why nothing was restored."""
    import inspect

    import app.main as main_mod

    assert "restore_and_connect" in inspect.getsource(main_mod.lifespan)
