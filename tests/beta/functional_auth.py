# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Beta functional tests — Multi-user auth (opt-in, requires CERID_MULTI_USER=true)."""

import uuid

import httpx
import pytest

MCP_BASE = "http://ai-companion-mcp:8888"
TIMEOUT = 30


@pytest.fixture(scope="module")
def test_user():
    """Generate unique test user credentials."""
    uid = uuid.uuid4().hex[:8]
    return {
        "email": f"beta-test-{uid}@cerid-test.local",
        "password": "BetaTest2026!Secure",
        "display_name": f"Beta Tester {uid}",
    }


@pytest.fixture(scope="module")
def auth_client():
    with httpx.Client(
        base_url=MCP_BASE,
        headers={"X-Client-ID": "beta-test", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    ) as c:
        yield c


@pytest.mark.p2
def test_f50_register(auth_client, test_user):
    """F-50: Register a new user account."""
    r = auth_client.post("/auth/register", json=test_user)
    # If multi-user is disabled, we may get 404 (route not registered)
    if r.status_code == 404:
        pytest.skip("Multi-user auth not enabled (CERID_MULTI_USER=false)")
    assert r.status_code in (200, 201), f"Register failed: {r.status_code} {r.text}"
    data = r.json()
    assert "access_token" in data or "user" in data


@pytest.mark.p2
def test_f51_login(auth_client, test_user):
    """F-51: Login with registered credentials."""
    # Register first (idempotent for this test)
    reg = auth_client.post("/auth/register", json=test_user)
    if reg.status_code == 404:
        pytest.skip("Multi-user auth not enabled")

    r = auth_client.post(
        "/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.p2
def test_f52_token_refresh(auth_client, test_user):
    """F-52: Refresh an access token."""
    reg = auth_client.post("/auth/register", json=test_user)
    if reg.status_code == 404:
        pytest.skip("Multi-user auth not enabled")

    login = auth_client.post(
        "/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    if login.status_code != 200:
        pytest.skip("Login failed, cannot test refresh")

    refresh_token = login.json().get("refresh_token")
    assert refresh_token, "No refresh token in login response"

    r = auth_client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 200, f"Refresh failed: {r.status_code} {r.text}"
    assert "access_token" in r.json()


@pytest.mark.p2
def test_f53_no_token_rejection(auth_client):
    """F-53: Accessing /auth/me without token should be rejected."""
    r = auth_client.get("/auth/me")
    if r.status_code == 404:
        pytest.skip("Multi-user auth not enabled")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


@pytest.mark.p2
def test_f54_valid_token_access(auth_client, test_user):
    """F-54: Accessing /auth/me with valid token should return user info."""
    reg = auth_client.post("/auth/register", json=test_user)
    if reg.status_code == 404:
        pytest.skip("Multi-user auth not enabled")

    login = auth_client.post(
        "/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    if login.status_code != 200:
        pytest.skip("Login failed")

    token = login.json().get("access_token")
    r = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert data.get("email") == test_user["email"]
