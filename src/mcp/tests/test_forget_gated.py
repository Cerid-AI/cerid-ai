# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forgetting from MCP clients and products: preview issues a token, execute spends it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Imported before any test patches preview_items: the router binds it at
# import time, and a router first imported under the patch would keep the fake.
import app.routers.forget  # noqa: F401
from app.services.forget import engine
from app.services.forget.adapters import PurgeResult
from tests.helpers.forget import isolate_forget

FIN = "f" * 64
PERSONAL = "p" * 64
FINANCE = {"X-Client-ID": "cerid-finance"}


class Noop:
    def __init__(self, kind: str) -> None:
        self.name, self.kinds = f"noop_{kind}", frozenset({kind})

    def hide(self, s, f):
        return None

    def restore(self, s, f):
        return None

    def purge(self, s):
        return PurgeResult(removed=1)


def _preview(subjects):
    """A preview that lists every subject it was given."""
    return {
        "subject": None, "title": "", "derived_facts": 0, "notes": [], "out_of_reach": [],
        "groups": [{"key": "documents", "default": "checked",
                    "items": [{"kind": s.kind, "id": s.id, "label": s.id[:6]} for s in subjects]}],
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.deps.get_redis", lambda: redis)
    monkeypatch.setattr("app.services.forget.preview.preview_items", _preview)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [Noop(kind)])
    arts = {FIN: {"id": FIN, "domain": "finance"}, PERSONAL: {"id": PERSONAL, "domain": "personal"}}
    monkeypatch.setattr("app.db.neo4j.artifacts.get_artifact", lambda driver, aid: arts.get(aid))
    monkeypatch.setattr("app.deps.get_neo4j", lambda: object())
    monkeypatch.setattr("app.services.private_mode.get_private_mode_level", lambda: 0)
    return {"reg": reg, "redis": redis}


@pytest.fixture
def sdk(env):
    from app.routers.sdk import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _preview_sdk(sdk, subjects, mode="trash", headers=FINANCE):
    return sdk.post("/sdk/v1/forget/preview", json={"subjects": subjects, "mode": mode}, headers=headers)


# ---- SDK ----

def test_a_product_previews_and_executes_within_its_domain(sdk, env):
    resp = _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confirm_token"] and body["expires_in"] == 900 and body["mode"] == "trash"
    assert env["reg"].state_of("artifact", FIN) is None  # a preview changes nothing

    done = sdk.post("/sdk/v1/forget/execute", json={"confirm_token": body["confirm_token"]}, headers=FINANCE)
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "trashed" and done.json()["subjects"] == 1
    entry = [e for e in env["reg"].latest() if e.subject.id == FIN][0]
    assert entry.state == "trashed" and entry.requested_by == "sdk"


def test_the_previewed_mode_is_the_one_that_runs(sdk, env):
    token = _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}], mode="permanent").json()["confirm_token"]
    done = sdk.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers=FINANCE)
    assert done.json()["state"] == "purged"
    assert env["reg"].state_of("artifact", FIN) == "purged"


def test_a_token_cannot_be_replayed_or_used_by_another_product(sdk, env):
    token = _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}]).json()["confirm_token"]
    other = sdk.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers={"X-Client-ID": "cerid-anneal"})
    assert other.status_code == 409
    again = sdk.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers=FINANCE)
    assert again.status_code == 409
    assert env["reg"].state_of("artifact", FIN) is None


@pytest.mark.parametrize("subject,status", [
    ({"kind": "artifact", "id": PERSONAL}, 403),
    ({"kind": "chunk", "id": f"{PERSONAL}_0123456789abcdef"}, 403),
    ({"kind": "memory", "id": "11111111-2222-3333-4444-555555555555"}, 403),
    ({"kind": "conversation", "id": "conv-1234"}, 403),
    ({"kind": "artifact", "id": "z" * 64}, 404),
])
def test_a_restricted_product_forgets_nothing_outside_its_domains(sdk, env, subject, status):
    resp = _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}, subject])
    assert resp.status_code == status, resp.text
    assert env["redis"].keys("cerid:forget:confirm:*") == []


def test_a_passage_in_the_products_domain_is_allowed(sdk, env, monkeypatch):
    monkeypatch.setattr("app.services.forget.preview.passage_ids", lambda ids: ids)
    resp = _preview_sdk(sdk, [{"kind": "chunk", "id": f"{FIN}_0123456789abcdef"}])
    assert resp.status_code == 200, resp.text


def test_malformed_requests_are_refused(sdk, env):
    assert _preview_sdk(sdk, []).status_code == 422
    assert _preview_sdk(sdk, [{"kind": "artifact", "id": "../x"}]).status_code == 422
    assert _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}], mode="shred").status_code == 422
    assert sdk.post("/sdk/v1/forget/execute", json={"confirm_token": ""}, headers=FINANCE).status_code == 422


# ---- MCP ----

@pytest.mark.asyncio
async def test_mcp_preview_then_execute(env):
    from app.mcp_tools import forget as tools

    out = await tools.pkb_forget_preview([{"kind": "artifact", "id": PERSONAL}], "trash")
    assert out["confirm_token"]
    assert env["reg"].state_of("artifact", PERSONAL) is None
    done = await tools.pkb_forget_execute(out["confirm_token"])
    assert done["state"] == "trashed"
    entry = [e for e in env["reg"].latest() if e.subject.id == PERSONAL][0]
    assert entry.requested_by == "mcp"


@pytest.mark.asyncio
async def test_mcp_refuses_a_spent_token(env):
    from app.mcp_tools import forget as tools
    from app.tool_registry import InvalidParamsError

    out = await tools.pkb_forget_preview([{"kind": "artifact", "id": PERSONAL}], "trash")
    await tools.pkb_forget_execute(out["confirm_token"])
    with pytest.raises(InvalidParamsError, match="already used"):
        await tools.pkb_forget_execute(out["confirm_token"])


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["search", "preview", "execute"])
async def test_mcp_refuses_in_multi_user_mode(env, monkeypatch, call):
    from app.mcp_tools import forget as tools
    from app.tool_registry import PermissionDeniedError

    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    args: dict[str, Any] = {
        "search": {"scope": "my 401k"},
        "preview": {"subjects": [{"kind": "artifact", "id": PERSONAL}], "mode": "trash"},
        "execute": {"confirm_token": "x" * 32},
    }[call]
    with pytest.raises(PermissionDeniedError):
        await getattr(tools, f"pkb_forget_{call}")(**args)


def test_the_confirm_token_is_redacted_from_tool_audit_logs():
    from app.tools import _summarize_args
    assert _summarize_args({"confirm_token": "secret-token-value"})["confirm_token"] == "<redacted>"


# ---- web route ----

def test_the_assist_route_returns_the_grouping(env, monkeypatch):
    from app.routers import forget as forget_router

    seen: dict[str, Any] = {}

    async def fake_assist(scope, *, allow_cloud=False, domains=None):
        seen.update(scope=scope, allow_cloud=allow_cloud)
        return {"status": "needs_consent", "model": None, "reason": "The local model is not available.",
                "cloud_model": "cloud/x", "scope": scope, "total": 0, "groups": []}

    monkeypatch.setattr("app.services.forget.assist.assist", fake_assist)
    app = FastAPI()
    app.include_router(forget_router.router)
    client = TestClient(app)
    resp = client.post("/forget/assist/search", json={"scope": "my old 401k"})
    assert resp.status_code == 200 and resp.json()["status"] == "needs_consent"
    assert seen == {"scope": "my old 401k", "allow_cloud": False}
    assert client.post("/forget/assist/search", json={"scope": "x"}).status_code == 422


@pytest.mark.parametrize("role,status", [("member", 403), ("admin", 200)])
def test_in_multi_user_mode_only_an_admin_forgets_through_the_sdk(env, monkeypatch, role, status):
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.routers.sdk import router

    monkeypatch.setattr("config.CERID_MULTI_USER", True)

    async def set_role(request, call_next):
        request.state.role = role
        return await call_next(request)

    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=set_role)
    app.include_router(router)
    client = TestClient(app)
    gui = {"X-Client-ID": "gui"}
    resp = client.post("/sdk/v1/forget/preview",
                       json={"subjects": [{"kind": "conversation", "id": "conv-1234"}], "mode": "trash"}, headers=gui)
    assert resp.status_code == status, resp.text
    executed = client.post("/sdk/v1/forget/execute", json={"confirm_token": "x" * 32}, headers=gui)
    assert executed.status_code == (403 if role == "member" else 409)


def test_a_token_is_bound_to_the_signed_in_user_too(env, monkeypatch):
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.routers.sdk import router

    monkeypatch.setattr("config.CERID_MULTI_USER", True)
    who = {"user": "u1"}

    async def set_user(request, call_next):
        request.state.role, request.state.user_id = "admin", who["user"]
        return await call_next(request)

    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=set_user)
    app.include_router(router)
    client = TestClient(app)
    token = client.post("/sdk/v1/forget/preview", json={"subjects": [{"kind": "artifact", "id": FIN}], "mode": "trash"},
                        headers=FINANCE).json()["confirm_token"]
    who["user"] = "u2"
    assert client.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers=FINANCE).status_code == 409
    assert env["reg"].state_of("artifact", FIN) is None


def test_a_token_survives_an_execute_that_could_not_start(sdk, env, monkeypatch):
    token = _preview_sdk(sdk, [{"kind": "artifact", "id": FIN}]).json()["confirm_token"]
    monkeypatch.setattr("config.SYNC_DIR", "")
    assert sdk.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers=FINANCE).status_code == 503
    monkeypatch.setattr("config.SYNC_DIR", str(env["reg"]._root.parent))
    done = sdk.post("/sdk/v1/forget/execute", json={"confirm_token": token}, headers=FINANCE)
    assert done.status_code == 200, done.text

