# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Client for the stack's rspamd service. Unset CERID_RSPAMD_URL disables the scan.

The scanner is the ``rspamd`` service in docker-compose.yml, on the stack
network and without a host port. The client accepts that service name
and loopback (a host-run process with its own scanner); any other host
is ignored, so the scan never leaves the machine.

The request disables reputation and authentication groups. A rebuilt
message has no SMTP session and no DKIM signature, and those checks
would also send sender domains off the machine.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from collections.abc import Mapping
from http import HTTPStatus
from http.client import HTTPConnection

SCAN_TIMEOUT_S = 2.0
_MAX_REPLY = 1_048_576
_DEFAULT_PORT = 11333
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_SERVICE_HOST = "rspamd"
_ALLOWED_HOSTS = _LOOPBACK | {_SERVICE_HOST}
_DISABLED_GROUPS = json.dumps({
    "groups_disabled": ["rbl", "surbl", "fuzzy", "fuzzy_check", "hfilter", "spf", "dkim", "dmarc"],
})


class RspamdScanError(RuntimeError):
    """The scanner answered, but not with a verdict."""


def rspamd_url() -> str:
    """http://rspamd:11333 or a loopback URL when configured. Any other host is ignored."""
    raw = os.getenv("CERID_RSPAMD_URL", "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.hostname or "").casefold().strip("[]")
    if parsed.scheme != "http" or host not in _ALLOWED_HOSTS or parsed.username or parsed.password:
        return ""
    return raw


def _endpoint(url: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "127.0.0.1").casefold().strip("[]")
    connect_host = "127.0.0.1" if host == "localhost" else host
    port = parsed.port or _DEFAULT_PORT
    path = parsed.path or "/checkv2"
    if path == "/":
        path = "/checkv2"
    return connect_host, port, path


def _post(raw: bytes) -> dict | None:
    """One /checkv2 call. A refused, slow, or non-200 scanner raises."""
    url = rspamd_url()
    if not url:
        return None
    host, port, path = _endpoint(url)
    headers = {
        "Content-Type": "text/plain",
        "User-Agent": "cerid-inbox",
        "Settings": _DISABLED_GROUPS,
    }
    password = os.getenv("CERID_RSPAMD_PASSWORD", "").strip()
    if password:
        headers["Password"] = password
    conn = HTTPConnection(host, port, timeout=SCAN_TIMEOUT_S)
    try:
        conn.request("POST", path, body=raw, headers=headers)
        response = conn.getresponse()
        body = response.read(_MAX_REPLY)
        if response.status != HTTPStatus.OK:
            raise RspamdScanError(f"rspamd replied HTTP {response.status}")
    finally:
        conn.close()
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


async def scan_if_configured(raw: bytes) -> dict | None:
    """POST /checkv2 when the URL names the service or loopback. Otherwise None.

    A failed scan raises (``OSError`` for refused or timed out,
    :class:`RspamdScanError` for a non-200 reply). The triage pass counts
    those and logs them once per run.
    """
    if not rspamd_url():
        return None
    return await asyncio.to_thread(_post, raw)


def _symbol_score(detail: object) -> float | None:
    if isinstance(detail, bool):
        return None
    if isinstance(detail, (int, float)):
        return float(detail)
    if isinstance(detail, Mapping):
        score = detail.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        return float(score)
    return None


def symbol_trace(payload: Mapping[str, object] | None) -> list[tuple[str, float]]:
    """Up to eight symbol names and scores. Options and URLs stay out."""
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("symbols")
    if not isinstance(raw, Mapping):
        return []
    scored: list[tuple[str, float]] = []
    for name, detail in raw.items():
        score = _symbol_score(detail)
        if score is None:
            continue
        scored.append((str(name), score))
    scored.sort(key=lambda item: abs(item[1]), reverse=True)
    return scored[:8]
