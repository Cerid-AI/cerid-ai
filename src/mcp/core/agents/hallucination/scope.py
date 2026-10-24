# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Which knowledge a verification may read as evidence.

Verification retrieves from the knowledge base and from memory to judge each
claim. It answers to the same request policy as a query: at Private Mode L2 it
reads neither, and a consumer verifies only against the domains its wall
admits. The transport sets the scope once (``guarded_retrieval.verification_scope``)
and every evidence lookup below reads it, so the scope reaches the per-claim
path and the batch pre-fetch without threading a parameter through both.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationScope:
    domains: frozenset[str] | None  # None = every owner domain
    memory: bool


_scope: ContextVar[VerificationScope | None] = ContextVar("verification_scope", default=None)


def kb_domains(base: list[str]) -> list[str]:
    """``base`` narrowed to the domains this verification may read."""
    scope = _scope.get()
    if scope is None or scope.domains is None:
        return base
    return [d for d in base if d in scope.domains]


def memory_allowed() -> bool:
    scope = _scope.get()
    return scope is None or scope.memory


def cache_partition() -> str:
    """The evidence a verdict could have rested on, as a claim-cache key part.

    A verdict reached for the owner's whole knowledge base must not be served to
    a consumer walled off from that evidence, nor one tenant's to another: the
    cached entry names its source artifact.
    """
    from core.context.identity import get_tenant_id

    scope = _scope.get()
    domains = "*" if scope is None or scope.domains is None else ",".join(sorted(scope.domains))
    memory = "m" if memory_allowed() else "-"
    return f"{get_tenant_id()}|{domains}|{memory}"
