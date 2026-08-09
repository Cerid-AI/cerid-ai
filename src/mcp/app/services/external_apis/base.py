# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Base class and shared utilities for public-API adapters (Phase API.1).

All concrete adapters extend :class:`ExternalAPIAdapter`.  The shared
HTTPX async client is created once per process via
:func:`get_http_client` and closed gracefully via
:func:`close_http_client` (called from ``app.main`` lifespan).

Error contract
--------------
Every adapter wraps HTTPX errors into :class:`ExternalAPIError` and
calls :func:`core.utils.swallowed.log_swallowed_error` before re-raising
so the swallowed-error counter stays accurate.  Callers can catch
:class:`ExternalAPIError` to handle the failure uniformly regardless of
which adapter raised.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx

from core.utils.swallowed import log_swallowed_error

# ---------------------------------------------------------------------------
# Shared HTTP client (one per process)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None
_http_lock = asyncio.Lock()

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)
_USER_AGENT = "cerid-ai/0.92.0 (+https://github.com/Cerid-AI/cerid-ai)"


async def get_http_client() -> httpx.AsyncClient:
    """Return (or lazily create) the shared async HTTP client.

    The client is intentionally process-scoped (not per-adapter) so we
    keep a single connection pool.  Thread-safe: protected by an asyncio
    lock so parallel startup paths don't double-create.
    """
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,  # follow_redirects: shared pool for fixed external-API hosts (adapter-supplied)
            )
    return _http_client


async def close_http_client() -> None:
    """Close the shared client on application shutdown."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class ExternalAPIError(Exception):
    """Raised when an external public-API call fails.

    Attributes
    ----------
    status_code:
        HTTP status that caused the failure, or 0 for transport errors.
    provider:
        The adapter slug that raised.
    detail:
        Human-readable description of the failure.
    """

    def __init__(self, provider: str, detail: str, status_code: int = 0) -> None:
        self.provider = provider
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"[{provider}] {detail} (HTTP {status_code})")


# ---------------------------------------------------------------------------
# Abstract adapter
# ---------------------------------------------------------------------------


class ExternalAPIAdapter(ABC):
    """Abstract base class for all eight public-API adapters.

    Class-level attributes define the adapter contract; concrete subclasses
    must populate them.  Abstract async methods are the minimal interface
    expected by :class:`app.services.external_apis.registry.ExternalAPIRegistry`.
    """

    slug: str = ""
    """Stable machine-readable identifier, e.g. ``"wikipedia"``."""

    display_name: str = ""
    """Human-readable name shown in the settings UI."""

    requires_key: bool = False
    """Whether a user-supplied API key is required for any calls."""

    key_env_var: str | None = None
    """Name of the environment variable that holds the API key, if any."""

    # ------------------------------------------------------------------
    # Required interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def lookup(self, *args: Any, **kwargs: Any) -> Any:
        """Primary lookup method; signature is adapter-specific."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` when the upstream endpoint is reachable.

        Must not raise — return ``False`` on any failure.
        """

    # ------------------------------------------------------------------
    # Shared error-wrapping helper
    # ------------------------------------------------------------------

    def _wrap_http_error(self, exc: Exception) -> ExternalAPIError:
        """Convert an HTTPX exception to :class:`ExternalAPIError` and log it.

        Called inside ``except httpx.HTTPError`` blocks.  Always returns the
        wrapped error so the caller can ``raise`` it; never re-raises
        internally (the caller controls the raise).
        """
        log_swallowed_error(f"external_apis.{self.slug}", exc)
        if isinstance(exc, httpx.HTTPStatusError):
            return ExternalAPIError(
                provider=self.slug,
                detail=str(exc),
                status_code=exc.response.status_code,
            )
        return ExternalAPIError(
            provider=self.slug,
            detail=str(exc),
            status_code=0,
        )
