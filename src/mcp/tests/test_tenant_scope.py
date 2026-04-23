# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tenant-scope enforcement gates for the RAG retrieval boundary.

Why this file exists: in multi-user mode, the tenant identity is captured
by ``app.middleware.tenant_context.TenantContextMiddleware`` and surfaced
to ``core/`` via ``core.context.identity.get_tenant_id``. Before the
2026-04-22 retrieval-boundary patch, the ``where`` filter passed into
ChromaDB was whatever the API caller supplied — a malformed filter or
a deliberately omitted one returned cross-tenant results.

This test enforces three guarantees that, taken together, prevent the
escape:

    1. ``with_tenant_scope`` always emits a clause that requires the
       chunk's ``tenant_id`` to match the active tenant — it is never a
       no-op.
    2. ``chunk_matches_tenant`` filters out chunks belonging to other
       tenants on the BM25-only fallback path (where ChromaDB's
       ``where`` clause is bypassed).
    3. The ``query_agent.multi_domain_query`` call site always passes
       ``where`` to ChromaDB (even when the caller passed no filter) —
       i.e. the helper is wired into the retrieval boundary, not just
       defined and unused.

These run against in-process fakes — no docker stack required. The
live-stack end-to-end variant is tracked as a follow-up
(`tests/integration/test_preservation_iX_tenant_isolation.py`).
"""
from __future__ import annotations

import ast
from types import SimpleNamespace
from typing import Any

import pytest

import config
from core.context.identity import (
    TenantScopeViolation,
    chunk_matches_tenant,
    tenant_id_var,
    with_tenant_scope,
)

# ---------------------------------------------------------------------------
# with_tenant_scope() — pure helper unit tests
# ---------------------------------------------------------------------------


class TestWithTenantScope:
    """Fusion semantics for the ChromaDB ``where`` clause."""

    def test_empty_filter_returns_tenant_only_clause(self):
        token = tenant_id_var.set("alice")
        try:
            assert with_tenant_scope(None) == {"tenant_id": "alice"}
            assert with_tenant_scope({}) == {"tenant_id": "alice"}
        finally:
            tenant_id_var.reset(token)

    def test_single_key_caller_filter_fuses_under_and(self):
        token = tenant_id_var.set("alice")
        try:
            result = with_tenant_scope({"domain": "coding"})
        finally:
            tenant_id_var.reset(token)

        assert result == {"$and": [{"tenant_id": "alice"}, {"domain": "coding"}]}

    def test_multi_key_caller_filter_preserves_caller_dict(self):
        token = tenant_id_var.set("alice")
        try:
            result = with_tenant_scope({"domain": "code", "filename": "x.py"})
        finally:
            tenant_id_var.reset(token)

        assert result == {
            "$and": [
                {"tenant_id": "alice"},
                {"domain": "code", "filename": "x.py"},
            ]
        }

    def test_caller_supplied_matching_tenant_is_passthrough(self):
        token = tenant_id_var.set("alice")
        try:
            result = with_tenant_scope({"tenant_id": "alice"})
        finally:
            tenant_id_var.reset(token)

        # Caller already in scope — no fusion needed, no double-key.
        assert result == {"tenant_id": "alice"}

    def test_caller_supplied_different_tenant_raises(self):
        token = tenant_id_var.set("alice")
        try:
            with pytest.raises(TenantScopeViolation, match="bob"):
                with_tenant_scope({"tenant_id": "bob"})
        finally:
            tenant_id_var.reset(token)


class TestChunkMatchesTenant:
    """Per-chunk tenant predicate for the BM25-only fallback path."""

    def test_matching_tenant(self):
        token = tenant_id_var.set("alice")
        try:
            assert chunk_matches_tenant({"tenant_id": "alice"}) is True
        finally:
            tenant_id_var.reset(token)

    def test_other_tenant_rejected(self):
        token = tenant_id_var.set("alice")
        try:
            assert chunk_matches_tenant({"tenant_id": "bob"}) is False
        finally:
            tenant_id_var.reset(token)

    def test_legacy_chunk_without_tenant_treated_as_default(self):
        """Pre-migration chunks have no ``tenant_id`` and belong to ``default``.

        A request running under the default tenant must still see them;
        a request under any non-default tenant must not.
        """
        from config.features import DEFAULT_TENANT_ID

        legacy_meta = {"domain": "coding"}
        default_token = tenant_id_var.set(DEFAULT_TENANT_ID)
        try:
            assert chunk_matches_tenant(legacy_meta) is True
        finally:
            tenant_id_var.reset(default_token)

        other_token = tenant_id_var.set("alice")
        try:
            assert chunk_matches_tenant(legacy_meta) is False
        finally:
            tenant_id_var.reset(other_token)

    def test_none_meta_rejected(self):
        token = tenant_id_var.set("alice")
        try:
            assert chunk_matches_tenant(None) is False
            assert chunk_matches_tenant({}) is False
        finally:
            tenant_id_var.reset(token)


# ---------------------------------------------------------------------------
# Integration: the helper is actually called from the retrieval boundary
# ---------------------------------------------------------------------------


def _empty_chroma_results() -> dict[str, Any]:
    """Shape that ChromaDB's ``collection.query`` returns when nothing matched."""
    return {
        "ids": [[]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }


class _FakeCollection:
    """Records every ``query`` invocation so the test can assert ``where``."""

    def __init__(self) -> None:
        self.query_calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.query_calls.append(kwargs)
        return _empty_chroma_results()


class _FakeChromaClient:
    def __init__(self, collection: _FakeCollection) -> None:
        self._collection = collection

    def list_collections(self) -> list[SimpleNamespace]:
        # Pretend every domain collection exists so ``multi_domain_query``
        # actually issues the query. Uses real attribute (``MagicMock(name=n)``
        # treats ``name`` as a Mock-init arg, not a settable attribute).
        return [
            SimpleNamespace(name=config.collection_name(d)) for d in config.DOMAINS
        ]

    def get_collection(self, name: str) -> _FakeCollection:
        return self._collection


def _disable_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the BM25 branch off so the test only asserts the vector path."""
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)


@pytest.mark.asyncio
async def test_multi_domain_query_always_passes_tenant_in_where(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vector-search call site MUST emit a ``where`` clause carrying tenant_id.

    Caller passed no ``metadata_filter`` — the historical bug was that the
    code path simply omitted ``where`` in this case. The patch makes
    ``with_tenant_scope`` always run, so ``where`` is always present.
    """
    from core.agents import query_agent

    _disable_bm25(monkeypatch)

    collection = _FakeCollection()
    client = _FakeChromaClient(collection)

    token = tenant_id_var.set("alice")
    try:
        await query_agent.multi_domain_query(
            query="any query",
            domains=["coding"],
            top_k=5,
            chroma_client=client,
            metadata_filter=None,
        )
    finally:
        tenant_id_var.reset(token)

    assert collection.query_calls, "collection.query was never invoked"
    where = collection.query_calls[0]["where"]
    # No caller filter → tenant-only clause.
    assert where == {"tenant_id": "alice"}, (
        f"vector-search where-clause does not enforce tenant scope: {where!r}"
    )


@pytest.mark.asyncio
async def test_multi_domain_query_fuses_tenant_with_caller_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-supplied filters are AND-fused with the tenant clause."""
    from core.agents import query_agent

    _disable_bm25(monkeypatch)

    collection = _FakeCollection()
    client = _FakeChromaClient(collection)

    token = tenant_id_var.set("alice")
    try:
        await query_agent.multi_domain_query(
            query="any query",
            domains=["coding"],
            top_k=5,
            chroma_client=client,
            metadata_filter={"filename": "report.pdf"},
        )
    finally:
        tenant_id_var.reset(token)

    where = collection.query_calls[0]["where"]
    assert where == {
        "$and": [{"tenant_id": "alice"}, {"filename": "report.pdf"}]
    }, f"expected fused $and clause, got {where!r}"


@pytest.mark.asyncio
async def test_multi_domain_query_blocks_caller_attempted_tenant_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller cannot escape the active tenant by spoofing ``tenant_id``.

    The retrieval helper must surface the violation rather than silently
    overriding — otherwise an audit cannot detect the attempt.
    """
    from core.agents import query_agent

    _disable_bm25(monkeypatch)

    collection = _FakeCollection()
    client = _FakeChromaClient(collection)

    token = tenant_id_var.set("alice")
    try:
        # Catch-all path inside multi_domain_query swallows per-domain
        # exceptions, so the query returns an empty list. The contract
        # we assert here is that ChromaDB was NEVER asked with a
        # cross-tenant where clause.
        results = await query_agent.multi_domain_query(
            query="any query",
            domains=["coding"],
            top_k=5,
            chroma_client=client,
            metadata_filter={"tenant_id": "bob"},
        )
    finally:
        tenant_id_var.reset(token)

    assert results == []
    for call in collection.query_calls:
        where = call.get("where", {})
        # Whatever the helper *did* allow through must not name another tenant.
        flat = _flatten_clauses(where)
        for clause in flat:
            if "tenant_id" in clause:
                assert clause["tenant_id"] == "alice", (
                    f"cross-tenant where clause leaked into ChromaDB: {clause!r}"
                )


def _flatten_clauses(where: Any) -> list[dict[str, Any]]:
    """Walk a possibly-nested ChromaDB ``where`` dict, return leaf clauses."""
    if not isinstance(where, dict):
        return []
    if "$and" in where:
        out: list[dict[str, Any]] = []
        for child in where["$and"]:
            out.extend(_flatten_clauses(child))
        return out
    return [where]


# ---------------------------------------------------------------------------
# Source-level invariant: every ChromaDB ``collection.query`` call site in
# core.agents.query_agent passes a ``where`` clause through ``with_tenant_scope``.
# This catches future call-site regressions where someone adds a new query
# without routing through the helper.
# ---------------------------------------------------------------------------


def test_query_agent_collection_query_calls_route_through_with_tenant_scope():
    """Static guard: every ``collection.query(..., where=...)`` in query_agent
    builds its ``where`` via ``with_tenant_scope``.

    Why source-level: the runtime tests above can prove the existing call
    sites are wired, but a brand-new call site shipped in a future PR
    wouldn't be exercised. This AST gate keeps the helper load-bearing.
    """
    from core.agents import query_agent as qa

    with open(qa.__file__) as fh:
        src = ast.parse(fh.read())

    violations: list[str] = []
    for node in ast.walk(src):
        # We care about Call nodes whose function ends in ``.query`` AND
        # which have a ``where`` keyword. Bare ``query()`` (without
        # ``where=``) is fine — ChromaDB defaults to no filter and the
        # vector-search call site always sets ``where`` via the helper.
        if not isinstance(node, ast.Call):
            continue
        is_query = isinstance(node.func, ast.Attribute) and node.func.attr == "query"
        if not is_query:
            continue
        for kw in node.keywords:
            if kw.arg != "where":
                continue
            if not _expression_uses_with_tenant_scope(kw.value):
                violations.append(
                    f"query_agent.py:{kw.value.lineno} — `where=` not routed through with_tenant_scope()"
                )

    # Also cover the dict-build-then-pass shape ``query_kwargs["where"] = ...``
    for node in ast.walk(src):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "where"
        ):
            continue
        if not _expression_uses_with_tenant_scope(node.value):
            violations.append(
                f"query_agent.py:{node.lineno} — `[...]['where'] = ...` not routed through with_tenant_scope()"
            )

    assert not violations, (
        "ChromaDB query call site bypassing tenant scope:\n  "
        + "\n  ".join(violations)
    )


def _expression_uses_with_tenant_scope(expr: ast.AST) -> bool:
    """True when ``expr`` is a call to ``with_tenant_scope(...)`` (any form)."""
    if not isinstance(expr, ast.Call):
        return False
    f = expr.func
    if isinstance(f, ast.Name) and f.id == "with_tenant_scope":
        return True
    if isinstance(f, ast.Attribute) and f.attr == "with_tenant_scope":
        return True
    return False


# ---------------------------------------------------------------------------
# Source-level invariant: every ``collection.add(..., metadatas=...)`` site
# in app.services.ingestion writes a metadata dict that includes ``tenant_id``.
# Catches the symmetric regression on the ingest side.
# ---------------------------------------------------------------------------


def test_ingestion_writes_tenant_id_into_chunk_metadata():
    """Every ingest path stamps ``tenant_id`` onto the chunk metadata.

    Without this, ``with_tenant_scope`` filters every newly-ingested
    chunk *out* of every query — the ingest side is the matching half
    of the retrieval-side guarantee.
    """
    from app.services import ingestion

    with open(ingestion.__file__) as fh:
        source = fh.read()

    # Cheap textual assertion — both base_meta dicts must mention tenant_id,
    # and the file must import get_tenant_id.
    assert "from core.context.identity import get_tenant_id" in source, (
        "ingestion module must import get_tenant_id"
    )
    occurrences = source.count('"tenant_id": get_tenant_id()')
    assert occurrences >= 2, (
        f"expected at least 2 base_meta sites stamping tenant_id, found {occurrences}"
    )


@pytest.mark.asyncio
async def test_event_loop_uses_default_tenant_outside_request() -> None:
    """Outside a request, ``get_tenant_id()`` returns the configured default.

    Background tasks (memory consolidation, scheduled jobs) run without a
    request context. They MUST still resolve to a known tenant — never
    to ``None`` — so retrieval inside those tasks remains scoped.
    """
    from config.features import DEFAULT_TENANT_ID
    from core.context.identity import get_tenant_id

    assert get_tenant_id() == DEFAULT_TENANT_ID
