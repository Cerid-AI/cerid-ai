#!/usr/bin/env python3
"""Cerid sign-in — minimal edge-auth service for the gateway and the web port.

Single-operator session auth. Caddy `forward_auth` calls /__auth/verify on every
request; a signed httpOnly cookie carries the session. The web container's nginx
asks /__auth/check instead, which answers 401 where verify answers a redirect:
`auth_request` treats anything but 2xx, 401 and 403 as a failure. Stdlib only (no deps) so it
runs on a bare python:3.12-slim with the script mounted — no image build.

Enablement mirrors CERID_API_KEY: if CERID_PORTAL_PASSWORD is unset, SSO is DISABLED
(verify always allows) so a local install keeps working out of the box. Set the password
to enforce a login.

Env:
  CERID_PORTAL_PASSWORD  the sign-in password (unset = SSO disabled)
  CERID_PORTAL_SECRET    HMAC signing key (optional; derived from the password if unset
                      so cookies stay valid across restarts as long as the password does)
  CERID_PORTAL_TTL       session lifetime in seconds (default 604800 = 7 days)
  CERID_PORTAL_PORT      listen port (default 8080)
  CERID_PORTAL_TITLE     name shown on the sign-in page (default "Cerid")
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = os.environ.get("CERID_PORTAL_PASSWORD", "")
# Programmatic clients (desktop companion, API consumers) authenticate with the
# companion's X-API-Key, not a browser cookie. Honor it at the edge so SSO doesn't
# lock them out. Empty = no key bypass (cookie is then the only way in).
API_KEY = os.environ.get("CERID_API_KEY", "")
_SECRET_ENV = os.environ.get("CERID_PORTAL_SECRET", "")
TTL = int(os.environ.get("CERID_PORTAL_TTL") or 604800)
PORT = int(os.environ.get("CERID_PORTAL_PORT") or 8080)
TITLE = os.environ.get("CERID_PORTAL_TITLE") or "Cerid"
COOKIE = "cerid_portal"
# Port 3000 is plain HTTP. Safari drops a Secure cookie there even on
# localhost, and every browser does on a LAN address, so that port gets a
# cookie of its own. Its token is signed under a different label: the gateway
# never accepts a session that has travelled in cleartext.
LOCAL_COOKIE = "cerid_portal_local"
_LOCAL_SCOPE = "local:"

# Stable signing key: explicit secret, else derived from the password. Either way it
# only exists when SSO is enabled, and survives restarts as long as the password does.
_SIGNING_KEY = (
    _SECRET_ENV.encode()
    if _SECRET_ENV
    else hashlib.sha256(b"cerid-portal-kdf:" + PASSWORD.encode()).digest()
)

ENABLED = bool(PASSWORD)

# Wrong-password limiting. Global, not per client: requests arrive through
# Caddy or nginx, so the peer address is the proxy's, and X-Forwarded-For is
# whatever the client chose to send. One password guards the whole install, so
# the bound that matters is total guesses. Each wrong password waits
# _FAIL_DELAY_S; after _FAIL_LIMIT of them inside _FAIL_WINDOW_S the form
# answers 429 until the oldest ages out. The operator keeps the API key and the
# tailnet identity while it is locked.
_FAIL_LIMIT = 20
_FAIL_WINDOW_S = 900
_FAIL_DELAY_S = 1.0
_failures: deque[float] = deque()
_failures_lock = threading.Lock()


def _locked_for(now: float) -> int:
    """Seconds until the form accepts another attempt; 0 when it is open."""
    with _failures_lock:
        while _failures and now - _failures[0] >= _FAIL_WINDOW_S:
            _failures.popleft()
        if len(_failures) < _FAIL_LIMIT:
            return 0
        return max(1, int(_failures[0] + _FAIL_WINDOW_S - now) + 1)


def _record_failure(now: float) -> None:
    with _failures_lock:
        _failures.append(now)


def _clear_failures() -> None:
    with _failures_lock:
        _failures.clear()


def _sign(exp: int, scope: str = "") -> str:
    return hmac.new(_SIGNING_KEY, f"{scope}{exp}".encode(), hashlib.sha256).hexdigest()


def _make_token(scope: str = "") -> str:
    exp = int(time.time()) + TTL
    return f"{exp}.{_sign(exp, scope)}"


def _valid_token(token: str, scope: str = "") -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(exp, scope))


def _authorize(
    *,
    enabled: bool,
    api_key_env: str,
    x_api_key: str,
    ts_user: str,
    cookie_token: str | None,
    local_cookie_token: str | None = None,
) -> bool:
    """Pure verify decision. Order: SSO-off → API key → Tailscale identity → cookie.

    ``local_cookie_token`` is the plain-HTTP port's cookie; only /__auth/check
    passes it.
    """
    if not enabled:
        return True
    if api_key_env and x_api_key and hmac.compare_digest(x_api_key, api_key_env):
        return True
    if ts_user:  # injected by `tailscale serve`; only reaches here on the tailnet listener
        return True
    if cookie_token and _valid_token(cookie_token):
        return True
    if local_cookie_token and _valid_token(local_cookie_token, _LOCAL_SCOPE):
        return True
    return False


def _cookie_value(cookie_header: str | None, name: str = COOKIE) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def _set_cookie(name: str, value: str, max_age: int) -> str:
    secure = "" if name == LOCAL_COOKIE else "Secure; "
    return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; {secure}Max-Age={max_age}"


def _safe_next(raw: str | None) -> str:
    """Only allow same-origin absolute paths; never an external/open redirect.

    Rejects protocol-relative (`//`, `/\\` — browsers normalize backslashes to
    slashes), anything with a scheme/host, and control/whitespace that could be
    smuggled into the Location header.
    """
    if not raw:
        return "/"
    raw = urllib.parse.unquote(raw)
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"
    if any(ord(c) < 0x20 or c in ("\\", " ") for c in raw):
        return "/"
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    return raw


_LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{title} — Sign in</title>
<style>
:root{{--bg:oklch(0.91 0.008 240);--card:oklch(0.97 0.004 240);--fg:oklch(0.13 0.02 240);--muted:oklch(0.30 0.02 240);--brand:oklch(0.46 0.14 185);--brand-fg:oklch(0.985 0 0);--brand-soft:oklch(0.46 0.14 185 / 0.12);--border:oklch(0.78 0.012 240);--ring:oklch(0.46 0.12 185);--down:oklch(0.58 0.19 25);--glow:oklch(0.46 0.14 185 / 0.10)}}
@media (prefers-color-scheme:dark){{:root{{--bg:oklch(0.16 0.03 240);--card:oklch(0.20 0.03 240);--fg:oklch(0.93 0.01 230);--muted:oklch(0.72 0.02 230);--brand:oklch(0.82 0.16 178);--brand-fg:oklch(0.12 0.03 240);--brand-soft:oklch(0.82 0.16 178 / 0.14);--border:oklch(0.42 0.04 230 / 0.45);--ring:oklch(0.72 0.14 178);--down:oklch(0.70 0.19 22);--glow:oklch(0.82 0.16 178 / 0.16)}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1.5rem;background:radial-gradient(60% 42% at 50% -6%,var(--glow),transparent 70%),var(--bg);color:var(--fg);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
form{{display:flex;flex-direction:column;align-items:stretch;gap:.9rem;width:20rem;padding:2rem 1.75rem;border:1px solid var(--border);border-radius:1rem;background:var(--card);box-shadow:0 1px 2px oklch(0 0 0 / .10),0 18px 40px oklch(0 0 0 / .18);text-align:center}}
.shield{{width:52px;height:52px;margin:0 auto .25rem;filter:drop-shadow(0 6px 16px var(--glow))}}
.shield svg{{width:100%;height:100%;display:block}}
h1{{font-size:1.15rem;font-weight:600;letter-spacing:-.01em;margin:0}}
p{{margin:0 0 .35rem;font-size:.82rem;color:var(--muted)}}
input[type=password]{{width:100%;padding:.72rem .85rem;border:1px solid var(--border);border-radius:.6rem;background:var(--bg);color:var(--fg);font-size:.95rem;outline:none;transition:border-color .15s,box-shadow .15s}}
input[type=password]:focus-visible{{border-color:var(--brand);box-shadow:0 0 0 3px var(--ring)}}
button{{width:100%;padding:.72rem;border:0;border-radius:.6rem;background:var(--brand);color:var(--brand-fg);font-size:.95rem;font-weight:600;cursor:pointer;transition:filter .15s,transform .15s}}
button:hover{{filter:brightness(1.06)}}button:active{{transform:translateY(1px)}}
button:focus-visible{{outline:none;box-shadow:0 0 0 3px var(--ring)}}
.err{{margin:0;color:var(--down);font-size:.8rem;font-weight:500}}
@media (prefers-reduced-motion:reduce){{*{{transition-duration:.001ms!important}}}}
</style>
</head><body>
<form method="POST" action="/__auth/login">
<div class="shield" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M16 2L4 7.5v9.5c0 6.8 5.1 12.8 12 14 6.9-1.2 12-7.2 12-14V7.5L16 2z" fill="var(--brand-soft)"/><path d="M16 2L4 7.5v9.5c0 6.8 5.1 12.8 12 14 6.9-1.2 12-7.2 12-14V7.5L16 2z" stroke="var(--brand)" stroke-width="1.1" fill="none"/><text x="16" y="21.6" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif" font-size="14.5" font-weight="700" fill="var(--brand)">C</text></svg></div>
<h1>{title}</h1><p>Enter the password to continue.</p>
{error}<input type="password" name="password" aria-label="Password" placeholder="Password" autofocus required autocomplete="current-password">
<input type="hidden" name="next" value="{next}"><button type="submit">Sign in</button></form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet; Caddy logs the edge
        pass

    def _send(self, code: int, body: bytes = b"", headers: dict | None = None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            for item in v if isinstance(v, list) else [v]:
                self.send_header(k, item)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _login_page(self, next_path: str, error: bool = False):
        err = '<p class="err">Incorrect password.</p>' if error else ""
        page = _LOGIN_HTML.format(
            error=err, next=next_path.replace('"', "%22"), title=html.escape(TITLE)
        )
        self._send(200, page.encode(), {"Content-Type": "text/html; charset=utf-8"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}

        if path in ("/__auth/verify", "/__auth/verify-tailnet"):
            # Tailscale identity is trusted ONLY on the tailnet verify path, which
            # Caddy invokes solely from the :8443 listener (reachable only via
            # `tailscale serve`). The public :443 forward_auth calls plain /verify,
            # so a client-forged Tailscale-User-Login at the public edge is ignored
            # — the trust boundary is the endpoint (bound to the listener), not a
            # strippable header.
            trust_ts = path == "/__auth/verify-tailnet"
            ts_user = self.headers.get("Tailscale-User-Login", "") if trust_ts else ""
            authorized = _authorize(
                enabled=ENABLED,
                api_key_env=API_KEY,
                x_api_key=self.headers.get("X-API-Key", ""),
                ts_user=ts_user,
                cookie_token=_cookie_value(self.headers.get("Cookie")),
            )
            if authorized:
                # Surface the verified tailnet identity downstream for audit.
                hdrs = {"X-Cerid-Identity": ts_user} if ts_user else None
                return self._send(200, headers=hdrs)
            # Tell the browser where to log in, preserving the original destination.
            orig = self.headers.get("X-Forwarded-Uri", "/")
            login = "/__auth/login?next=" + urllib.parse.quote(orig, safe="")
            return self._send(302, headers={"Location": login})

        if path == "/__auth/check":
            cookies = self.headers.get("Cookie")
            authorized = _authorize(
                enabled=ENABLED,
                api_key_env=API_KEY,
                x_api_key=self.headers.get("X-API-Key", ""),
                ts_user="",
                cookie_token=_cookie_value(cookies),
                local_cookie_token=_cookie_value(cookies, LOCAL_COOKIE),
            )
            access = "signed-in" if ENABLED else "open"
            return self._send(
                200 if authorized else 401, headers={"X-Cerid-Port-Access": access}
            )

        if path == "/__auth/login":
            if not ENABLED:
                return self._send(302, headers={"Location": "/"})
            return self._login_page(_safe_next(query.get("next", ["/"])[0]))

        if path == "/__auth/logout":
            expired = [_set_cookie(COOKIE, "", 0), _set_cookie(LOCAL_COOKIE, "", 0)]
            return self._send(302, headers={"Location": "/__auth/login", "Set-Cookie": expired})

        return self._send(404, b"not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/__auth/login":
            return self._send(404, b"not found")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        form = urllib.parse.parse_qs(body)
        submitted = form.get("password", [""])[0]
        next_path = _safe_next(form.get("next", ["/"])[0])

        if not ENABLED:
            return self._send(302, headers={"Location": "/"})
        wait = _locked_for(time.time())
        if wait:
            return self._send(
                429,
                b"Too many sign-in attempts. Try again later.",
                {"Content-Type": "text/plain; charset=utf-8", "Retry-After": str(wait)},
            )
        if hmac.compare_digest(submitted, PASSWORD):
            _clear_failures()
            # Set by the web container's nginx, which serves plain HTTP. A
            # caller who sends it through the gateway only costs themselves a
            # cookie the gateway will not accept.
            if self.headers.get("X-Cerid-Edge") == "local":
                cookie = _set_cookie(LOCAL_COOKIE, _make_token(_LOCAL_SCOPE), TTL)
            else:
                cookie = _set_cookie(COOKIE, _make_token(), TTL)
            return self._send(303, headers={"Location": next_path, "Set-Cookie": cookie})
        _record_failure(time.time())
        time.sleep(_FAIL_DELAY_S)
        return self._login_page(next_path, error=True)


def main():
    mode = "ENFORCED" if ENABLED else "DISABLED (CERID_PORTAL_PASSWORD unset)"
    print(f"cerid-sso: listening on :{PORT} — SSO {mode}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
