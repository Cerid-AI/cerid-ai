# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared SSRF-guarded HTTP fetch for source connectors.

Any connector that fetches an *operator-supplied* URL (rss, url_watch, …) is an
SSRF vector — an internal target like ``http://ai-companion-neo4j:7474`` or the
cloud metadata endpoint ``http://169.254.169.254/`` must be refused even on an
"internal-only" deployment. This is the one canonical guard so the protection
can't drift between connectors (copy-pasted security code rots).

Guard: allowlist http(s); resolve ALL A/AAAA records and reject if ANY is
loopback / private / link-local / reserved / multicast / unspecified; disable
auto-redirects and re-validate every ``Location`` hop (a 3xx target is
attacker-controlled too). Residual: a DNS-rebinding race between this resolve
and httpx's own resolve is not closed here (full pinning needs a custom
transport + SNI handling); no-auto-redirect + per-hop revalidation closes the
common direct + redirect SSRF paths.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # cap untrusted body (memory / DoS guard)


def is_blocked_ip(ip_str: str) -> bool:
    """True if an IP must not be fetched (loopback / private / link-local /
    reserved / multicast / unspecified — the SSRF target ranges)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_fetchable(url: str) -> None:
    """SSRF guard for an operator-supplied URL. Raises ValueError unless the URL
    is http(s) AND every resolved A/AAAA address is public. Resolving ALL records
    and rejecting if ANY is internal defeats split-horizon / multi-record tricks.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked url scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("url has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"dns resolution failed for {host!r}: {exc}") from exc
    addrs = {str(info[4][0]) for info in infos}
    if not addrs:
        raise ValueError(f"no addresses resolved for {host!r}")
    blocked = sorted(a for a in addrs if is_blocked_ip(a))
    if blocked:
        raise ValueError(
            f"refusing internal/private address(es) {blocked} for {host!r} (SSRF guard)"
        )


async def guarded_get(
    url: str,
    *,
    method: str = "GET",
    user_agent: str = "CeridAI-Connector/1.0",
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """SSRF-guarded fetch. Validates the target, disables auto-redirects, and
    manually follows up to ``MAX_REDIRECTS`` hops re-validating each Location.
    Raises ValueError on a blocked target / redirect loop / oversize body;
    httpx.HTTPError on network failure. Returns the final (non-redirect)
    response with its body fully loaded.

    ``headers`` (optional) are merged over the ``User-Agent`` so a caller can
    send request hints (e.g. ``Accept``) without giving up the guard.

    The response is streamed and the download is aborted once it exceeds
    ``max_bytes`` — the cap is enforced DURING the read, not after the whole
    (attacker-controlled) body has been buffered into memory.
    """
    current = url
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,  # validate every hop ourselves
        headers={"User-Agent": user_agent, **(headers or {})},
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            await asyncio.to_thread(assert_fetchable, current)  # blocking DNS off the loop
            async with client.stream(method, current) as resp:
                location = resp.headers.get("location")
                if resp.is_redirect and location:
                    current = urljoin(current, location)
                    continue
                total = 0
                chunks: list[bytes] = []
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            f"response body exceeds {max_bytes} byte cap (DoS guard)"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                # aiter_bytes yields content-decoded bytes, so the original
                # framing headers no longer describe `body`; drop them to keep
                # the returned response self-consistent for callers.
                resp_headers = httpx.Headers(
                    [
                        (k, v)
                        for k, v in resp.headers.items()
                        if k.lower()
                        not in ("content-encoding", "content-length", "transfer-encoding")
                    ]
                )
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp_headers,
                    content=body,
                    request=resp.request,
                )
    raise ValueError(f"too many redirects (> {MAX_REDIRECTS})")
