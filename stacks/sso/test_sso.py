import http.client
import importlib
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

# Import sso.py with SSO ENABLED so _authorize's enabled-path is exercised.
os.environ["CERID_PORTAL_PASSWORD"] = "test-pw"  # pragma: allowlist secret
os.environ["CERID_API_KEY"] = "test-api-key"  # pragma: allowlist secret
sys.path.insert(0, os.path.dirname(__file__))
sso = importlib.import_module("sso")


def A(**kw):
    base = dict(enabled=True, api_key_env="test-api-key",  # pragma: allowlist secret
               x_api_key="", ts_user="", cookie_token=None)
    base.update(kw)
    return sso._authorize(**base)


def test_disabled_allows_everything():
    assert A(enabled=False) is True

def test_blocks_when_no_credentials():
    assert A() is False

def test_valid_api_key_bypass():
    assert A(x_api_key="test-api-key") is True  # pragma: allowlist secret

def test_wrong_api_key_blocked():
    assert A(x_api_key="nope") is False  # pragma: allowlist secret

def test_tailscale_identity_bypass():
    assert A(ts_user="someone@example") is True

def test_empty_tailscale_identity_blocked():
    assert A(ts_user="") is False


# ── Port 3000: the web container's proxy asks /__auth/check ─────────────────
# Driven over a real socket: what matters is the status line and the cookie
# attributes a browser would see.

PASSWORD = "test-pw"  # pragma: allowlist secret
LOCAL_EDGE = {"X-Cerid-Edge": "local"}


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), sso.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address
    httpd.shutdown()


def call(server, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection(*server, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    resp.text = resp.read().decode()
    conn.close()
    return resp


def login(server, headers=None):
    resp = call(
        server, "POST", "/__auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        body=f"password={PASSWORD}&next=/",
    )
    assert resp.status == 303
    return resp.getheader("Set-Cookie")


def pair(set_cookie):
    return set_cookie.split(";", 1)[0]


def attributes(set_cookie):
    return [a.strip().lower() for a in set_cookie.split(";")[1:]]


def test_check_answers_401_not_a_redirect(server):
    resp = call(server, "GET", "/__auth/check")
    assert resp.status == 401
    assert resp.getheader("Location") is None


def test_check_accepts_the_api_key(server):
    resp = call(server, "GET", "/__auth/check", {"X-API-Key": "test-api-key"})  # pragma: allowlist secret
    assert resp.status == 200


def test_check_refuses_a_wrong_api_key(server):
    assert call(server, "GET", "/__auth/check", {"X-API-Key": "nope"}).status == 401  # pragma: allowlist secret


def test_check_ignores_a_tailscale_identity(server):
    resp = call(server, "GET", "/__auth/check", {"Tailscale-User-Login": "someone@example"})
    assert resp.status == 401


def test_check_says_sign_in_is_enforced(server):
    resp = call(server, "GET", "/__auth/check", {"X-API-Key": "test-api-key"})  # pragma: allowlist secret
    assert resp.getheader("X-Cerid-Port-Access") == "signed-in"


def test_check_says_the_port_is_open_when_no_password_is_set(server, monkeypatch):
    monkeypatch.setattr(sso, "ENABLED", False)
    resp = call(server, "GET", "/__auth/check")
    assert resp.status == 200
    assert resp.getheader("X-Cerid-Port-Access") == "open"


def test_gateway_login_keeps_the_secure_cookie(server):
    cookie = login(server)
    assert pair(cookie).startswith("cerid_portal=")
    assert "secure" in attributes(cookie)
    assert "httponly" in attributes(cookie)


def test_plain_http_login_sets_a_cookie_the_browser_will_keep(server):
    cookie = login(server, LOCAL_EDGE)
    assert pair(cookie).startswith("cerid_portal_local=")
    assert "secure" not in attributes(cookie)
    assert "httponly" in attributes(cookie)
    assert "samesite=lax" in attributes(cookie)


def test_check_accepts_either_cookie(server):
    for cookie in (login(server), login(server, LOCAL_EDGE)):
        assert call(server, "GET", "/__auth/check", {"Cookie": pair(cookie)}).status == 200


@pytest.mark.parametrize("path", ["/__auth/verify", "/__auth/verify-tailnet"])
def test_the_gateway_does_not_accept_the_plain_http_cookie(server, path):
    local = pair(login(server, LOCAL_EDGE))
    assert call(server, "GET", path, {"Cookie": local}).status == 302


@pytest.mark.parametrize("path", ["/__auth/verify", "/__auth/verify-tailnet", "/__auth/check"])
def test_a_plain_http_token_is_not_valid_under_the_gateway_cookie_name(server, path):
    token = pair(login(server, LOCAL_EDGE)).split("=", 1)[1]
    resp = call(server, "GET", path, {"Cookie": f"cerid_portal={token}"})
    assert resp.status in (302, 401)


def test_a_gateway_token_is_not_valid_under_the_plain_http_cookie_name(server):
    token = pair(login(server)).split("=", 1)[1]
    resp = call(server, "GET", "/__auth/check", {"Cookie": f"cerid_portal_local={token}"})
    assert resp.status == 401


def test_wrong_password_sets_no_cookie(server):
    resp = call(
        server, "POST", "/__auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded", **LOCAL_EDGE},
        body="password=wrong&next=/",
    )
    assert resp.status == 200
    assert resp.getheader("Set-Cookie") is None


def test_logout_clears_both_cookies(server):
    resp = call(server, "GET", "/__auth/logout", LOCAL_EDGE)
    cleared = resp.headers.get_all("Set-Cookie")
    assert sorted(pair(c) for c in cleared) == ["cerid_portal=", "cerid_portal_local="]
    by_name = {pair(c): attributes(c) for c in cleared}
    assert "secure" in by_name["cerid_portal="]
    assert "secure" not in by_name["cerid_portal_local="]


def test_the_sign_in_page_names_no_product_by_default(server):
    page = call(server, "GET", "/__auth/login").text
    assert "<title>Cerid — Sign in</title>" in page
    assert "<h1>Cerid</h1>" in page
    assert "suite" not in page.lower()


def test_the_sign_in_page_takes_its_name_from_the_environment(server, monkeypatch):
    monkeypatch.setattr(sso, "TITLE", "Example Name")
    page = call(server, "GET", "/__auth/login").text
    assert "<title>Example Name — Sign in</title>" in page
    assert "<h1>Example Name</h1>" in page


def test_a_configured_name_cannot_inject_markup(server, monkeypatch):
    monkeypatch.setattr(sso, "TITLE", "<script>alert(1)</script>")
    page = call(server, "GET", "/__auth/login").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
