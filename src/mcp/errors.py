# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Canonical error module for Cerid AI.

Every domain-specific exception in the project inherits from :class:`CeridError`.
Import errors from here — do **not** define ad-hoc exceptions elsewhere.
"""
from __future__ import annotations

__all__ = [
    "CeridError",
    "IngestionError",
    "RetrievalError",
    "VerificationError",
    "RoutingError",
    "SyncError",
    "ProviderError",
    "CreditExhaustedError",
    "RateLimitError",
    "ConfigError",
    "FeatureGateError",
    "StorageLimitExceededError",
    "error_response",
]


class CeridError(Exception):
    """Base exception for all Cerid AI errors.

    Parameters
    ----------
    message:
        Human-readable description of the error.
    error_code:
        Machine-readable code (e.g. ``"RETRIEVAL_CHROMA_TIMEOUT"``).
    details:
        Optional structured payload for debugging / API consumers.
    """

    _default_prefix: str = "CERID_"
    http_status: int = 500  # default HTTP status when rendered by the app handler

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code: str = error_code or f"{self._default_prefix}ERROR"
        self.details: dict | None = details


class IngestionError(CeridError):
    """Parse failures, deduplication conflicts, and chunking errors."""

    _default_prefix: str = "INGESTION_"


class RetrievalError(CeridError):
    """ChromaDB, Neo4j, or embedding lookup failures."""

    _default_prefix: str = "RETRIEVAL_"


class VerificationError(CeridError):
    """Claim extraction or verdict parsing failures."""

    _default_prefix: str = "VERIFICATION_"


class RoutingError(CeridError):
    """Model selection, Bifrost proxy, or Ollama routing failures."""

    _default_prefix: str = "ROUTING_"


class SyncError(CeridError):
    """Import, export, or manifest synchronisation failures."""

    _default_prefix: str = "SYNC_"


class ProviderError(CeridError):
    """LLM provider communication or response errors."""

    _default_prefix: str = "PROVIDER_"


class CreditExhaustedError(ProviderError):
    """Upstream provider returned HTTP 402 — credits or quota exhausted."""

    _default_prefix: str = "PROVIDER_CREDIT_"
    http_status: int = 402


class RateLimitError(ProviderError):
    """Upstream provider returned HTTP 429 — rate limit exceeded."""

    _default_prefix: str = "PROVIDER_RATE_"
    http_status: int = 429


class ConfigError(CeridError):
    """Missing environment variables or invalid configuration values."""

    _default_prefix: str = "CONFIG_"


class FeatureGateError(CeridError):
    """Feature requires a higher tier (e.g. pro or enterprise)."""

    _default_prefix: str = "FEATURE_GATE_"
    http_status: int = 403


class StorageLimitExceededError(CeridError):
    """Corpus storage is at/above ``STORAGE_CRITICAL_PCT`` (AF-042).

    Ingest backpressure only — rejects new ingest so the corpus cannot grow
    past the configured limit. Does NOT evict or delete existing content;
    eviction is a separate, destructive policy decision with its own design.
    """

    _default_prefix: str = "STORAGE_"
    http_status: int = 507


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def error_response(exc: CeridError) -> dict:
    """Convert a :class:`CeridError` into a dict suitable for FastAPI JSON responses.

    Returns
    -------
    dict
        ``{"error_code": str, "message": str, "details": dict | None}``
    """
    return {
        "error_code": exc.error_code,
        "message": str(exc),
        "details": exc.details,
    }
