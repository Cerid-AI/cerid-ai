# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SSO Foundation — SAML/OIDC integration stubs and helpers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("ai-companion.enterprise.sso")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SSOConfig:
    """SSO provider configuration."""

    provider: str = ""  # "saml" | "oidc"
    metadata_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    attribute_mapping: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SAML (stub — requires xmlsec native dependency)
# ---------------------------------------------------------------------------

def validate_saml_assertion(xml: str) -> dict:
    """Parse a SAML assertion and extract user attributes.

    Raises :class:`NotImplementedError` because full SAML validation
    requires the ``xmlsec`` native library which is not bundled by default.
    """
    raise NotImplementedError(
        "SAML assertion validation requires the xmlsec dependency. "
        "Install python3-saml and xmlsec1 system library, then implement "
        "this function for your IdP."
    )


# ---------------------------------------------------------------------------
# OIDC helpers
# ---------------------------------------------------------------------------

async def get_oidc_discovery(metadata_url: str) -> dict:
    """Fetch the OpenID Connect discovery document from *metadata_url*.

    Returns the parsed JSON as a dict.  Only HTTPS URLs are accepted
    to prevent SSRF against internal services.
    """
    from urllib.parse import urlparse

    parsed = urlparse(metadata_url)
    if parsed.scheme != "https":
        raise ValueError("OIDC metadata URL must use HTTPS")

    import httpx  # deferred import

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(metadata_url)
        resp.raise_for_status()
        return resp.json()


def _validate_token_endpoint(url: str) -> None:
    """Reject token endpoints targeting internal/private networks (SSRF prevention)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Token endpoint must use HTTPS: {url}")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError(f"Token endpoint has no hostname: {url}")
    try:
        for info in socket.getaddrinfo(hostname, None):
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValueError(f"Token endpoint resolves to private/internal IP: {hostname}")
    except socket.gaierror:
        pass  # DNS resolution failure handled at POST time


async def exchange_oidc_code(code: str, config: SSOConfig) -> dict:
    """Exchange an OIDC authorization code for tokens.

    Sends a POST to the token endpoint discovered from
    ``config.metadata_url`` and returns the token response dict.
    """
    import httpx  # deferred import

    discovery = await get_oidc_discovery(config.metadata_url)
    token_endpoint = discovery.get("token_endpoint", "")
    if not token_endpoint:
        raise ValueError("No token_endpoint found in OIDC discovery document")

    # Validate token_endpoint: must be HTTPS and not resolve to private IPs
    _validate_token_endpoint(token_endpoint)

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(token_endpoint, data=payload)
        resp.raise_for_status()
        return resp.json()
