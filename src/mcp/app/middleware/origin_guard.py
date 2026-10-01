# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Refuse writes that a browser sends on behalf of another site.

The web container's proxy adds the API key to every request it forwards, so
by the time a request reaches this process the key says nothing about who
asked. ``Origin`` does: a browser sets it on every cross-origin write and a
page cannot change it. Callers that are not browsers send none and pass.

The server's own origin is matched on host and port only. Behind the gateway
and ``tailscale serve`` TLS ends before the request arrives, so the scheme
seen here is ``http`` for a page the browser loaded over ``https``.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import config

logger = logging.getLogger("ai-companion.origin")

_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_SPELLINGS = {"localhost": "127.0.0.1", "127.0.0.1": "localhost"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
# What the desktop app sends: its bundle is loaded from disk. A browser sends
# "null" for a page opened from disk, so this admits no web page.
_DESKTOP_ORIGIN = "file://"


def _configured_origins() -> set[str]:
    return {
        o.strip() for o in getattr(config, "CORS_ORIGINS", "").split(",") if o.strip()
    }


def cors_origin_allowed(request: Request) -> bool:
    """True unless the request carries an Origin the app's CORS policy rejects."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = _configured_origins()
    return "*" in allowed or origin in allowed


def _loopback_aliases(origins: set[str]) -> set[str]:
    aliases = set()
    for origin in origins:
        parts = urlsplit(origin)
        other = _LOOPBACK_SPELLINGS.get(parts.hostname or "")
        if other:
            port = f":{parts.port}" if parts.port else ""
            aliases.add(f"{parts.scheme}://{other}{port}")
    return aliases


def _is_own_origin(origin: str, host_header: str) -> bool:
    try:
        page = urlsplit(origin)
        server = urlsplit(f"//{host_header}")
        default_port = _DEFAULT_PORTS.get(page.scheme)
        page_port = page.port or default_port
        server_port = server.port or default_port
    except ValueError:
        return False
    if not page.hostname or not server.hostname or default_port is None:
        return False
    return page.hostname == server.hostname and page_port == server_port


def _refusal(request: Request) -> str | None:
    origin = request.headers.get("origin", "")
    configured = _configured_origins()
    named = origin != "" and (
        origin == _DESKTOP_ORIGIN
        or origin in configured
        or origin in _loopback_aliases(configured)
        or _is_own_origin(origin, request.headers.get("host", ""))
    )
    if origin and not named and "*" not in configured:
        return (
            f"Cross-site request refused: origin {origin!r} may not change data "
            "on this server. If the origin is yours, add it to CORS_ORIGINS."
        )
    if request.headers.get("sec-fetch-site") == "cross-site" and not named:
        return (
            f"Cross-site request refused: the browser marked this request from "
            f"origin {origin!r} as cross-site. If the origin is yours, add it to "
            "CORS_ORIGINS by name."
        )
    return None


class OriginGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _STATE_CHANGING:
            detail = _refusal(request)
            if detail is not None:
                logger.warning(
                    "Refused cross-site %s %s from origin %r",
                    request.method, request.url.path, request.headers.get("origin"),
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": detail, "error_code": "CROSS_SITE_REQUEST_REFUSED"},
                )
        return await call_next(request)
