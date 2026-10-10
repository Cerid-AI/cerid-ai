# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``POST /memories/dedup`` reports the supersessions the lineage writer applied.

The endpoint once counted every attempt as one memory superseded. The writer
can refuse (the older memory was superseded meanwhile, or a version is gone) or
fail outright, and neither may be reported as landed, nor stop the other pairs.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.lineage.writer import SupersedeResult

_NEW = {"id": "mem-new", "text": "SOL price is down 1.23% today", "created_at": "2026-08-12T10:00:00+00:00"}
_OLD = {"id": "mem-old", "text": "SOL price down 1.38%", "created_at": "2026-08-12T09:00:00+00:00"}


class _Session:
    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def run(self, cypher: str, **params):
        return iter([_NEW, _OLD])


class _Driver:
    def session(self) -> _Session:
        return _Session()


def _dedup(monkeypatch: pytest.MonkeyPatch, **writer) -> tuple[dict, list]:
    from app.main import app

    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: _Driver())
    monkeypatch.setattr("app.routers.memories.get_chroma", lambda: object())
    with patch("core.lineage.writer.supersede", **writer) as supersede:
        res = TestClient(app, raise_server_exceptions=False).post("/memories/dedup", json={"confirm": True})
    assert res.status_code == 200, res.text
    return res.json(), supersede.call_args_list


def test_a_refused_supersession_is_not_counted(monkeypatch):
    data, calls = _dedup(monkeypatch, return_value=SupersedeResult(False, reason="already superseded"))
    assert len(calls) == 1, "the write was never attempted"
    assert data["duplicate_groups"] == 1 and data["memories_superseded"] == 0


def test_a_supersession_that_failed_is_not_counted(monkeypatch):
    data, calls = _dedup(monkeypatch, side_effect=RuntimeError("graph unavailable"))
    assert len(calls) == 1
    assert data["memories_superseded"] == 0


def test_an_applied_supersession_is_counted(monkeypatch):
    data, calls = _dedup(monkeypatch, return_value=SupersedeResult(True, lineage_id="mem-old", version=2))
    assert calls[0].args[2:] == ("mem-old", "mem-new")
    assert calls[0].kwargs["valid_to"] == _NEW["created_at"]
    assert data["memories_superseded"] == 1


def _dedup_as(monkeypatch: pytest.MonkeyPatch, role: str, confirm: bool) -> tuple[int, list]:
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.routers.memories import router

    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: _Driver())
    monkeypatch.setattr("app.routers.memories.get_chroma", lambda: object())

    async def set_role(request, call_next):
        request.state.role = role
        return await call_next(request)

    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=set_role)
    app.include_router(router)
    with patch("core.lineage.writer.supersede", return_value=SupersedeResult(True)) as supersede:
        res = TestClient(app).post("/memories/dedup", json={"confirm": confirm})
    return res.status_code, supersede.call_args_list


def test_in_multi_user_mode_only_an_admin_rewrites_memory_history(monkeypatch):
    status, calls = _dedup_as(monkeypatch, "member", confirm=True)
    assert status == 403 and calls == []
    assert _dedup_as(monkeypatch, "member", confirm=False)[0] == 200  # a dry run changes nothing
    status, calls = _dedup_as(monkeypatch, "admin", confirm=True)
    assert status == 200 and len(calls) == 1
