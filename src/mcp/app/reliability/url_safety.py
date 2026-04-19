# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SSRF guard — reject URLs whose host resolves to private / loopback / metadata addresses.

Used by any outbound-HTTP code path that accepts user-supplied URLs:
  - utils/data_sources/rss_feed.py  (custom RSS feeds added by the user)
  - utils/data_sources/custom.py    (user-defined API data sources)
  - utils/webhooks.py               (user-configured webhook endpoints)

The guard resolves the hostname (including A/AAAA records) and rejects
if ANY resolved address is private, loopback, link-local, reserved, or
multicast. Multi-address hostnames are rejected if any single address
fails — avoids DNS-rebinding tricks that alternate public/private.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import sentry_sdk

logger = logging.getLogger("ai-companion.url_safety")


def is_private_host(url: str) -> bool:
    """Return True if the URL's host resolves to a private / internal address.

    Any of the following address categories triggers a True return:
      - loopback (127.0.0.0/8, ::1)
      - private (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)
      - link-local (169.254.0.0/16 — includes cloud-metadata 169.254.169.254)
      - reserved (0.0.0.0/8, 240.0.0.0/4, etc.)
      - multicast (224.0.0.0/4, ff00::/8)

    Malformed URLs, DNS-resolution failures, and missing hostnames all
    return True — "treat as suspicious" is the safer default.
    """
    host = urlparse(url).hostname
    if host is None:
        return True

    try:
        # getaddrinfo returns all A/AAAA records; reject if ANY is private
        # to defeat DNS-rebinding tricks.
        addresses = socket.getaddrinfo(host, None)
    except (OSError, socket.gaierror):
        return True

    for _family, _type, _proto, _canonname, sockaddr in addresses:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Non-IP sockaddr value — treat as suspicious
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


def guard_or_log(url: str, *, source_name: str) -> bool:
    """Gate outbound fetches: return True if safe to fetch, False if blocked.

    On block, emits a structured log warning + Sentry event tagged with
    the source name (e.g. ``"rss_feed"``). Callers typically return an
    empty result list on False so the pipeline degrades gracefully.
    """
    if is_private_host(url):
        logger.warning(
            "%s.ssrf_blocked",
            source_name,
            extra={"url": url},
        )
        sentry_sdk.capture_message(
            f"{source_name}.ssrf_blocked",
            level="warning",
        )
        return False
    return True
