# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""`/auth/register` must not hand out admin to whoever asks first.

Gating the endpoint on the API key moved the bar to "can reach the server",
and on the deployment that gate exists for the key is not a secret: compose
injects VITE_CERID_API_KEY into the served UI's window.__ENV__ and
src/web/src/lib/api/common.ts sends it, so every browser that loads the GUI
holds it. Loopback registration is exempt from the key entirely. Behind both,
the role assignment was untouched: any request carrying `tenant_name` minted
role="admin".

The privilege decision belongs in the handler, and it has exactly three
legitimate sources: genuine first run (no users exist), an existing admin's
token, or the operator's bootstrap token.

Note for anyone extending this: JWTAuthMiddleware exempts the whole `/auth/`
prefix, so `request.state.role` is never populated on this route. A check
written against it would refuse every real admin and pass every attacker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import APIKeyMiddleware
from app.middleware.jwt_auth import create_access_token, decode_access_token
from config.features import DEFAULT_TENANT_ID

# The token helpers bind CERID_JWT_SECRET as a default argument at import, so
# without this the suite signs with whatever the environment held when
# config.features was first imported — empty on a clean runner, which PyJWT
# refuses. Sign and verify with one explicit secret instead.
_JWT_SECRET = "register-admin-bootstrap-test-secret-0123456789"  # pragma: allowlist secret

ADMIN = {"id": "admin-1", "email": "admin@example.com", "role": "admin",
         "tenant_id": "t-1"}
MEMBER = {"id": "member-1", "email": "member@example.com", "role": "member",
          "tenant_id": "t-1"}


@pytest.fixture
def calls():
    """Every users.py function the handler touches, patched at its import site."""
    with (
        patch("app.routers.auth.get_neo4j", return_value=MagicMock()),
        patch("app.routers.auth.get_redis", return_value=MagicMock()),
        patch("app.routers.auth.get_user_by_email", return_value=None),
        patch("app.routers.auth.get_tenant", return_value={"id": DEFAULT_TENANT_ID}),
        patch("app.routers.auth.create_tenant") as create_tenant,
        patch("app.routers.auth.update_last_login"),
        patch("app.routers.auth.any_user_exists") as any_user_exists,
        patch("app.routers.auth.get_user_by_id") as get_user_by_id,
        patch("app.routers.auth.create_user") as create_user,
    ):
        create_user.side_effect = lambda driver, **kw: {"id": "new-1", **kw}
        any_user_exists.return_value = True
        get_user_by_id.return_value = None
        yield {
            "create_user": create_user,
            "create_tenant": create_tenant,
            "any_user_exists": any_user_exists,
            "get_user_by_id": get_user_by_id,
        }


@pytest.fixture
def client(monkeypatch):
    """The loopback shape: no API key, /auth/register exempt from the middleware."""
    monkeypatch.setenv("CERID_BIND_ADDR", "127.0.0.1")
    monkeypatch.delenv("CERID_API_KEY", raising=False)
    monkeypatch.delenv("CERID_BOOTSTRAP_TOKEN", raising=False)

    import app.routers.auth as auth_mod

    monkeypatch.setattr(
        auth_mod, "create_access_token",
        lambda payload: create_access_token(payload, secret=_JWT_SECRET),
    )
    monkeypatch.setattr(
        auth_mod, "decode_access_token",
        lambda token: decode_access_token(token, secret=_JWT_SECRET),
    )

    app = FastAPI()
    app.include_router(auth_mod.router)
    app.add_middleware(APIKeyMiddleware, api_key=None)
    return TestClient(app)


def _body(**over):
    return {
        "email": "someone@example.com",
        "password": "hunter2-hunter2",  # pragma: allowlist secret
        **over,
    }


def _role_of(create_user) -> str:
    assert create_user.call_count == 1, create_user.call_args_list
    return create_user.call_args.kwargs["role"]


# ---------------------------------------------------------------------------
# The escalation
# ---------------------------------------------------------------------------

def test_anonymous_tenant_registration_is_refused(client, calls):
    """The GUI's own key is public on a LAN deployment, and loopback needs
    none at all — so an unauthenticated caller must not be able to mint one."""
    resp = client.post("/auth/register", json=_body(tenant_name="Acme"))

    assert resp.status_code == 403, resp.text
    calls["create_user"].assert_not_called()
    calls["create_tenant"].assert_not_called()


def test_member_cannot_mint_an_admin(client, calls):
    """A provisioned non-admin user is still not an administrator."""
    calls["get_user_by_id"].return_value = MEMBER
    token = create_access_token({"sub": MEMBER["id"], "role": "admin",
                                 "tenant_id": MEMBER["tenant_id"]},
                                secret=_JWT_SECRET)

    resp = client.post(
        "/auth/register",
        json=_body(tenant_name="Acme"),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403, resp.text
    calls["create_user"].assert_not_called()


def test_registration_without_a_tenant_stays_a_member(client, calls):
    resp = client.post("/auth/register", json=_body())

    assert resp.status_code == 200, resp.text
    assert _role_of(calls["create_user"]) == "member"


# ---------------------------------------------------------------------------
# The three legitimate ways to become one
# ---------------------------------------------------------------------------

def test_first_run_bootstrap_mints_the_operator(client, calls):
    """No users exist: this is the install, and somebody has to be admin."""
    calls["any_user_exists"].return_value = False

    resp = client.post("/auth/register", json=_body(tenant_name="Acme"))

    assert resp.status_code == 200, resp.text
    assert _role_of(calls["create_user"]) == "admin"


def test_first_user_of_the_default_tenant_is_admin(client, calls):
    """Otherwise a fresh install with no tenant_name has no administrator."""
    calls["any_user_exists"].return_value = False

    resp = client.post("/auth/register", json=_body())

    assert resp.status_code == 200, resp.text
    assert _role_of(calls["create_user"]) == "admin"


def test_an_existing_admin_may_create_a_tenant(client, calls):
    calls["get_user_by_id"].return_value = ADMIN
    token = create_access_token({"sub": ADMIN["id"], "role": "admin",
                                 "tenant_id": ADMIN["tenant_id"]},
                                secret=_JWT_SECRET)

    resp = client.post(
        "/auth/register",
        json=_body(tenant_name="Acme"),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    assert _role_of(calls["create_user"]) == "admin"


def test_bootstrap_token_may_create_a_tenant(client, calls, monkeypatch):
    monkeypatch.setenv("CERID_BOOTSTRAP_TOKEN", "operator-token-xyz")  # pragma: allowlist secret

    resp = client.post(
        "/auth/register",
        json=_body(tenant_name="Acme"),
        headers={"X-Bootstrap-Token": "operator-token-xyz"},
    )

    assert resp.status_code == 200, resp.text
    assert _role_of(calls["create_user"]) == "admin"


def test_wrong_bootstrap_token_is_refused(client, calls, monkeypatch):
    monkeypatch.setenv("CERID_BOOTSTRAP_TOKEN", "operator-token-xyz")  # pragma: allowlist secret

    resp = client.post(
        "/auth/register",
        json=_body(tenant_name="Acme"),
        headers={"X-Bootstrap-Token": "guess"},
    )

    assert resp.status_code == 403, resp.text
    calls["create_user"].assert_not_called()


def test_unset_bootstrap_token_is_not_a_blank_password(client, calls):
    """An empty CERID_BOOTSTRAP_TOKEN must not match an empty header."""
    resp = client.post(
        "/auth/register",
        json=_body(tenant_name="Acme"),
        headers={"X-Bootstrap-Token": ""},
    )

    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# The first-run predicate itself
# ---------------------------------------------------------------------------

def test_any_user_exists_reads_the_graph():
    from app.db.neo4j.users import any_user_exists

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {"exists": False}
    assert any_user_exists(driver) is False

    session.run.return_value.single.return_value = {"exists": True}
    assert any_user_exists(driver) is True

    query = session.run.call_args.args[0]
    assert "User" in query and "count" in query.lower(), query
