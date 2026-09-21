# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
API key authentication middleware.

Checks X-API-Key header against CERID_API_KEY env var.

The exemptions come in two grades. ``EXEMPT_PATHS``/``EXEMPT_PREFIXES`` are
unconditional: minimal probes plus the login surface, which carry no operator
data and must answer before anyone holds a credential. Everything in the
``_LOOPBACK_ONLY_*`` sets is exempt only while the server is bound to loopback,
where "reachable" already means "local process."

A missing key is not an exemption. Binding off loopback with ``CERID_API_KEY``
empty is refused at import time — see ``assert_auth_boundary_configured`` —
rather than served unauthenticated.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.features import DEFAULT_TENANT_ID

logger = logging.getLogger("ai-companion.auth")

# Unconditionally exempt. /health/ping and /health/live are the two probes the
# Docker HEALTHCHECK and load balancers hit; both return a fixed minimal
# payload and neither has a way to present a header.
EXEMPT_PATHS = {
    "/health/ping",
    "/health/live",
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/.well-known/agent.json",
}
# /auth/login and /auth/refresh must answer before the caller holds anything —
# that is the whole point of a login endpoint. /auth/register is NOT in this
# grade; see below.
EXEMPT_PREFIXES = ("/auth/",)

# Exempt only while the server is bound to loopback, where "reachable" already
# means "local process." In LAN mode (CERID_BIND_ADDR=0.0.0.0) that assumption
# is false, and each of these would otherwise be unauthenticated to the whole
# network, contradicting docs/LAN_REMOTE_ACCESS.md:
#   /mcp/, /a2a/  — every MCP tool, including deletes and purges. Loopback
#                   stays exempt for headerless local clients (.mcp.json).
#   /health, /health/  — not probes. They return the knowledge-pack registry
#                   path, the KB domain names, swallowed-error counters,
#                   whether field encryption is on, and the internal
#                   inference URL. The two real probes are exempt above.
#   /auth/register  — creates an account and returns a session, and creates a
#                   tenant with role "admin" when tenant_name is supplied. It
#                   is account creation, not login. Loopback keeps first-run
#                   bootstrap on the operator's own machine working.
_LOOPBACK_ONLY_EXEMPT_PATHS = frozenset({"/health"})
_LOOPBACK_ONLY_EXEMPT_PREFIXES = ("/health/", "/mcp/", "/a2a/", "/auth/register")


_LOOPBACK_ADDRS = ("127.0.0.1", "::1", "localhost")

# Runtime-written markers. A container cannot see its own publish mapping, so
# these are the only local evidence that "127.0.0.1" is not the whole story.
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def _in_container() -> bool:
    """True when this process is running inside a container."""
    if os.getenv("CERID_IN_CONTAINER", "").strip() not in ("", "0", "false"):
        return True
    return any(os.path.exists(marker) for marker in _CONTAINER_MARKERS)


def _declared_bind() -> str:
    """The bind address the operator declared, or "" when they declared none.

    Read through ``os.environ`` membership rather than a ``getenv`` default:
    "unset" has to stay distinguishable from "set to the loopback default",
    and that distinction is the whole of the container check below.
    """
    if "CERID_BIND_ADDR" not in os.environ:
        return ""
    return os.environ["CERID_BIND_ADDR"].strip()


def _is_loopback_bind() -> bool:
    """True when the server can only be reached from the local host.

    The variable declares intent, not fact: inside the image uvicorn always
    binds 0.0.0.0 (Dockerfile CMD) and reachability is decided by the publish
    mapping, which is invisible from in here. `docker run -p 0.0.0.0:8888:8888`
    without ``CERID_BIND_ADDR`` is LAN-exposed, so an ABSENT variable inside a
    container is not evidence of loopback — it is the absence of evidence.
    Compose always propagates the variable (docker-compose.yml), which is what
    keeps the loopback stack keyless.
    """
    declared = _declared_bind()
    if not declared:
        return not _in_container()
    return declared in _LOOPBACK_ADDRS


_SERVER_ENTRYPOINTS = ("uvicorn", "gunicorn", "hypercorn")


def _is_server_process() -> bool:
    """True when this process was started to serve the application."""
    if any("app.main" in arg for arg in sys.argv):
        return True
    return Path(sys.argv[0]).name in _SERVER_ENTRYPOINTS


def assert_auth_boundary_configured(api_key: str | None = None) -> None:
    """Refuse an unauthenticated server that is reachable off the local host.

    Called at import time (below) as well as from ``APIKeyMiddleware``. The
    middleware alone is too late: it is constructed while Starlette builds its
    stack, i.e. during lifespan startup, and uvicorn's default ``--lifespan
    auto`` downgrades a lifespan error to "protocol appears unsupported", logs
    "Application startup complete", binds the port and serves 500s. Raising on
    import makes the PROCESS exit non-zero before anything listens.
    """
    key = api_key if api_key is not None else os.getenv("CERID_API_KEY", "")
    if key or _is_loopback_bind():
        return
    declared = _declared_bind()
    where = (
        f"CERID_BIND_ADDR={declared!r} publishes the API beyond loopback"
        if declared
        else "This process runs in a container with no CERID_BIND_ADDR, so its "
        "published port may be reachable from the network"
    )
    raise RuntimeError(
        f"{where} but CERID_API_KEY is empty. Refusing to serve an "
        "unauthenticated knowledge base on the network.\n"
        "        Generate a key:  openssl rand -hex 32\n"
        "        Then add to .env: CERID_API_KEY=<key>   "
        "(and set it in the desktop client)\n"
        "        Or bind to loopback: CERID_BIND_ADDR=127.0.0.1"
    )


def _redact_ip(ip: str) -> str:
    """Hash-redact an IP address for safe logging."""
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


class APIKeyMiddleware(BaseHTTPMiddleware):
    _warned_no_key: bool = False

    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self.api_key = api_key or os.getenv("CERID_API_KEY", "")
        # Bind address is fixed for the process lifetime — resolve once.
        self._loopback_bind = _is_loopback_bind()
        assert_auth_boundary_configured(self.api_key)
        if not self.api_key and not APIKeyMiddleware._warned_no_key:
            logger.warning("API key auth is disabled — all requests will pass through unauthenticated")
            APIKeyMiddleware._warned_no_key = True
        if not self._loopback_bind:
            logger.info("Non-loopback bind — MCP/A2A/health/register surfaces require X-API-Key")

    def _is_exempt(self, path: str) -> bool:
        if path in EXEMPT_PATHS:
            return True
        if (
            path in _LOOPBACK_ONLY_EXEMPT_PATHS
            or path.startswith(_LOOPBACK_ONLY_EXEMPT_PREFIXES)
        ):
            return self._loopback_bind
        return path.startswith(EXEMPT_PREFIXES)

    async def dispatch(self, request: Request, call_next):
        # A CORS preflight cannot carry X-API-Key — the spec strips custom
        # headers from it — so 401ing OPTIONS would break every cross-origin
        # browser and SDK caller the moment a key is set. Let it reach
        # CORSMiddleware; the actual request that follows is still checked.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth if no key configured. Only reachable on a loopback bind —
        # __init__ refuses the non-loopback pairing outright.
        if not self.api_key:
            return await call_next(request)

        path = request.url.path

        if self._is_exempt(path):
            return await call_next(request)

        # Check header
        provided = request.headers.get("X-API-Key", "")
        if not provided or not hmac.compare_digest(provided, self.api_key):
            client = request.client.host if request.client else "unknown"
            logger.warning(f"Unauthorized request to {path} from {_redact_ip(client)}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


def get_current_user(request: Request) -> tuple[str | None, str]:
    """Extract (user_id, tenant_id) from request state.

    Returns (None, DEFAULT_TENANT_ID) when no user is authenticated
    (single-user mode or unauthenticated request).
    """
    user_id = getattr(request.state, "user_id", None)
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
    return user_id, tenant_id


# Enforced at import, not at first request: ``app/main.py`` imports this module
# at module scope, so `uvicorn app.main:app` exits non-zero here instead of
# binding a port it must not serve. Scoped to the serving process — a pytest
# run inside a container (docs/CONTRIBUTING.md runs the suite that way) imports
# this module too, and it binds nothing.
if _is_server_process():
    assert_auth_boundary_configured()
