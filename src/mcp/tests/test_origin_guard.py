# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A web page the user merely visited must not be able to change data (audit 66).

The web container's proxy adds the API key to every request it forwards, so
the key proves nothing about who is asking. What a browser cannot forge is the
``Origin`` it sends with a write. These tests drive the real application and
its real middleware stack; the probe path does not exist, so a request that
gets past the guard answers 404 (or 401 when a key is configured) and one that
does not answers the guard's own 403.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PROBE = "/__origin_guard_probe__"
EVIL = "https://evil.example"
WRITES = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _refused(resp) -> bool:
    if resp.status_code != 403:
        return False
    return resp.json().get("error_code") == "CROSS_SITE_REQUEST_REFUSED"


@pytest.mark.parametrize("method", WRITES)
def test_write_from_a_foreign_origin_is_refused(client: TestClient, method: str):
    resp = client.request(method, PROBE, headers={"Origin": EVIL})
    assert _refused(resp), (resp.status_code, resp.text)
    assert EVIL in resp.json()["detail"]


def test_write_to_a_real_route_is_refused_before_it_runs(client: TestClient):
    resp = client.post(
        "/admin/kb/clear-domain", json={"domain": "code"}, headers={"Origin": EVIL}
    )
    assert _refused(resp), (resp.status_code, resp.text)


def test_the_refusal_carries_a_request_id(client: TestClient):
    resp = client.post(PROBE, headers={"Origin": EVIL})
    assert _refused(resp)
    assert resp.headers.get("x-request-id")


def test_opaque_origin_is_refused(client: TestClient):
    assert _refused(client.post(PROBE, headers={"Origin": "null"}))


@pytest.mark.parametrize("method", WRITES)
def test_write_with_no_origin_passes(client: TestClient, method: str):
    """The SDK, the CLI and every other non-browser caller send no Origin."""
    assert not _refused(client.request(method, PROBE))


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8888",
    ],
)
def test_write_from_a_configured_origin_passes(client: TestClient, origin: str):
    assert not _refused(client.post(PROBE, headers={"Origin": origin}))


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8888",
    ],
)
def test_the_other_loopback_spelling_of_a_configured_origin_passes(
    client: TestClient, origin: str
):
    assert not _refused(client.post(PROBE, headers={"Origin": origin}))


def test_loopback_alias_keeps_the_port_and_scheme(client: TestClient):
    assert _refused(client.post(PROBE, headers={"Origin": "http://127.0.0.1:9999"}))
    assert _refused(client.post(PROBE, headers={"Origin": "https://127.0.0.1:3000"}))


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("192.168.1.50:3000", "http://192.168.1.50:3000"),
        ("192.168.1.50", "https://192.168.1.50"),
        ("node.example.ts.net:8443", "https://node.example.ts.net:8443"),
        ("[fd00::1]:3000", "http://[fd00::1]:3000"),
        ("MacPro.local:3000", "http://macpro.local:3000"),
    ],
)
def test_write_from_the_servers_own_origin_passes(
    client: TestClient, host: str, origin: str
):
    resp = client.post(PROBE, headers={"Host": host, "Origin": origin})
    assert not _refused(resp), resp.text


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("192.168.1.50:3000", "http://192.168.1.50:4000"),
        ("192.168.1.50:3000", "http://192.168.1.51:3000"),
        ("192.168.1.50", "http://192.168.1.50:3000"),
        ("192.168.1.50:3000", "http://192.168.1.50.evil.example:3000"),
    ],
)
def test_a_different_host_or_port_is_not_the_servers_own_origin(
    client: TestClient, host: str, origin: str
):
    resp = client.post(PROBE, headers={"Host": host, "Origin": origin})
    assert _refused(resp), (resp.status_code, resp.text)


def test_forwarded_host_does_not_make_an_origin_the_servers_own(client: TestClient):
    """Any caller can send X-Forwarded-Host; nothing here trusts it."""
    resp = client.post(
        PROBE,
        headers={
            "Origin": EVIL,
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        },
    )
    assert _refused(resp), (resp.status_code, resp.text)


def test_cross_site_write_is_refused_without_an_origin(client: TestClient):
    resp = client.post(PROBE, headers={"Sec-Fetch-Site": "cross-site"})
    assert _refused(resp), (resp.status_code, resp.text)


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
def test_other_fetch_sites_pass(client: TestClient, site: str):
    assert not _refused(client.post(PROBE, headers={"Sec-Fetch-Site": site}))


def test_a_configured_origin_may_be_cross_site(client: TestClient):
    """Listing an origin in CORS_ORIGINS is the operator saying it is theirs."""
    resp = client.post(
        PROBE,
        headers={"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"},
    )
    assert not _refused(resp), resp.text


def test_wildcard_cors_does_not_admit_a_cross_site_write(
    client: TestClient, monkeypatch
):
    import config

    monkeypatch.setattr(config, "CORS_ORIGINS", "*")
    assert not _refused(client.post(PROBE, headers={"Origin": EVIL}))
    resp = client.post(
        PROBE, headers={"Origin": EVIL, "Sec-Fetch-Site": "cross-site"}
    )
    assert _refused(resp), (resp.status_code, resp.text)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_reads_are_not_the_guards_business(client: TestClient, method: str):
    resp = client.request(
        method, PROBE, headers={"Origin": EVIL, "Sec-Fetch-Site": "cross-site"}
    )
    assert resp.status_code != 403, resp.text


def test_preflight_still_reaches_cors(client: TestClient):
    resp = client.options(
        "/settings",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_preflight_from_a_foreign_origin_is_cors_to_answer(client: TestClient):
    resp = client.options(
        "/settings",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "PATCH"},
    )
    assert not _refused(resp)
    assert "access-control-allow-origin" not in resp.headers


def test_mcp_transport_keeps_its_stricter_rule(client: TestClient):
    """/mcp/* grants only what CORS grants: no loopback alias, no own origin."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
    resp = client.post(
        "/mcp/call-sync", json=body, headers={"Origin": "http://127.0.0.1:3000"}
    )
    assert resp.status_code == 403
    assert resp.text == "origin not allowed"


# The desktop app loads its bundle from disk (packages/desktop main.ts,
# loadFile) and calls the API directly. A page opened from disk in a browser
# sends "null", which stays refused: so does every sandboxed frame on the web.
DESKTOP = "file://"
# What the signed 1.0.8 desktop build actually sends on a cross-origin write,
# captured from its renderer on 2026-10-02: no Origin at all. The guard admitted
# only "file://" and refused every desktop chat as cross-site.
DESKTOP_RENDERER = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


@pytest.mark.parametrize("method", WRITES)
def test_the_packaged_desktop_renderer_may_write(client: TestClient, method: str):
    resp = client.request(method, PROBE, headers=DESKTOP_RENDERER)
    assert not _refused(resp), resp.text


@pytest.mark.parametrize("mode", ["navigate", "no-cors", "same-origin", None])
def test_a_cross_site_write_without_an_origin_outside_cors_mode_is_refused(
    client: TestClient, mode
):
    headers = {"Sec-Fetch-Site": "cross-site"}
    if mode:
        headers["Sec-Fetch-Mode"] = mode
    assert _refused(client.post(PROBE, headers=headers))


@pytest.mark.parametrize("origin", ["null", "http://evil.example"])
def test_a_cors_write_with_an_unnamed_origin_is_still_refused(client: TestClient, origin: str):
    assert _refused(client.post(PROBE, headers={**DESKTOP_RENDERER, "Origin": origin}))


@pytest.mark.parametrize("method", WRITES)
def test_the_desktop_app_may_write(client: TestClient, method: str):
    resp = client.request(
        method, PROBE, headers={"Origin": DESKTOP, "Sec-Fetch-Site": "cross-site"}
    )
    assert not _refused(resp), resp.text


def test_the_desktop_app_needs_no_wildcard(client: TestClient):
    import config

    assert "*" not in config.CORS_ORIGINS
    assert DESKTOP not in config.CORS_ORIGINS
    assert not _refused(client.post(PROBE, headers={"Origin": DESKTOP}))


@pytest.mark.parametrize(
    "origin", ["file://evil.example", "file:///etc/passwd", "FILE://", "null", "app://x"]
)
def test_nothing_that_merely_resembles_the_desktop_origin_may_write(
    client: TestClient, origin: str
):
    assert _refused(client.post(PROBE, headers={"Origin": origin}))
