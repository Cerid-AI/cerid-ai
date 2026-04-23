# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tenant/user identity context — propagated via contextvars across async boundaries.

Lives in ``core`` (not ``app``) so retrieval-layer enforcement can read the
active tenant without crossing the import-linter ``core must not import app``
contract. The Starlette middleware that *populates* these contextvars from
request state lives in ``app/middleware/tenant_context.py`` because middleware
is web-framework-specific; that module re-exports ``tenant_id_var`` and
``get_tenant_id`` from here for backwards compatibility.

The ``with_tenant_scope`` helper is the single retrieval-time enforcement
boundary: every ChromaDB ``where`` clause that flows out of an agent must
pass through it so a missing/malformed caller filter cannot return chunks
belonging to a different tenant.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

from config.features import DEFAULT_TENANT_ID

logger = logging.getLogger("ai-companion.context.identity")


tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tenant_id", default=DEFAULT_TENANT_ID
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id", default=None
)


def get_tenant_id() -> str:
    """Current tenant ID (DEFAULT_TENANT_ID outside a request context)."""
    return tenant_id_var.get()


def get_user_id() -> str | None:
    """Current user ID (None outside a request or in single-user mode)."""
    return user_id_var.get()


# ---------------------------------------------------------------------------
# Retrieval-layer scope enforcement
# ---------------------------------------------------------------------------


class TenantScopeViolation(RuntimeError):
    """Caller-supplied filter attempted to escape the active tenant scope."""


_TENANT_KEY = "tenant_id"


def with_tenant_scope(where: dict[str, Any] | None) -> dict[str, Any]:
    """Fuse the active ``tenant_id`` into a ChromaDB ``where`` clause.

    Always returns a dict containing a tenant equality match. When the
    caller passes additional filters, they are AND-fused with the tenant
    filter using ChromaDB's ``$and`` operator.

    Raises ``TenantScopeViolation`` when the caller-supplied filter
    explicitly names a different ``tenant_id`` (a security failure that
    must surface, not silently override).
    """
    active = get_tenant_id()
    if not where:
        return {_TENANT_KEY: active}

    if _TENANT_KEY in where and where[_TENANT_KEY] != active:
        logger.error(
            "Tenant scope escape attempt: caller filter tenant_id=%r, active tenant=%r",
            where[_TENANT_KEY], active,
        )
        raise TenantScopeViolation(
            f"caller supplied tenant_id={where[_TENANT_KEY]!r} but active tenant is {active!r}"
        )
    if _TENANT_KEY in where:
        # Same key + same value → caller already in scope, no fusion needed.
        return where

    return {"$and": [{_TENANT_KEY: active}, where]}


def chunk_matches_tenant(meta: dict[str, Any] | None) -> bool:
    """Return True iff a chunk's metadata belongs to the active tenant.

    Used on the BM25-only fallback path where ChromaDB's ``where`` clause
    is bypassed (results come from the local BM25 index, then their
    metadata is fetched separately). Chunks ingested before the
    multi-tenant migration have no ``tenant_id`` and are treated as
    belonging to the default tenant.
    """
    if not meta:
        return False
    chunk_tenant = meta.get(_TENANT_KEY, DEFAULT_TENANT_ID)
    return bool(chunk_tenant == get_tenant_id())
