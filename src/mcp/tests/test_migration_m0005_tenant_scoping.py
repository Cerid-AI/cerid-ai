# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""m0005 tenant-scoping migration (Phase 1a) — DDL + backfill contract.

Guards the migration's shape: one tenant index + one default-tenant
backfill per content label, idempotent, Community-Edition-safe (plain
single-property index, no composite constraint). No live Neo4j — the
driver is mocked.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from app.db.neo4j.migrations import m0005_tenant_scoping
from config.features import DEFAULT_TENANT_ID


class _RecordingDriver:
    """Minimal Neo4j driver stub that records every Cypher statement run."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict] = []

    @contextmanager
    def session(self):
        sess = MagicMock()

        def _run(cypher, **kw):
            self.statements.append(cypher)
            self.params.append(kw)
            return MagicMock()

        sess.run.side_effect = _run
        yield sess


_LABELS = m0005_tenant_scoping._CONTENT_LABELS


def test_m0005_index_and_backfill_per_label() -> None:
    driver = _RecordingDriver()
    result = m0005_tenant_scoping.run(driver)

    assert result == {"tenant_indexes": len(_LABELS), "labels_backfilled": len(_LABELS)}
    joined = "\n".join(driver.statements)
    for label in _LABELS:
        assert f"{label.lower()}_tenant_idx" in joined
        assert f"FOR (n:{label}) ON (n.tenant_id)" in joined
        # Each label gets exactly one index + one backfill statement.
    assert len(driver.statements) == 2 * len(_LABELS)


def test_m0005_backfill_uses_default_tenant() -> None:
    driver = _RecordingDriver()
    m0005_tenant_scoping.run(driver)
    backfills = [s for s in driver.statements if "SET n.tenant_id" in s]
    assert len(backfills) == len(_LABELS)
    # The default tenant flows as a bound param, not a literal.
    assert any(p.get("default") == DEFAULT_TENANT_ID for p in driver.params)


def test_m0005_is_idempotent() -> None:
    """Indexes are IF NOT EXISTS; backfills are guarded by IS NULL so a
    re-run touches nothing already stamped."""
    driver = _RecordingDriver()
    m0005_tenant_scoping.run(driver)
    for stmt in driver.statements:
        if "CREATE INDEX" in stmt:
            assert "IF NOT EXISTS" in stmt
        elif "SET n.tenant_id" in stmt:
            assert "WHERE n.tenant_id IS NULL" in stmt


def test_m0005_indexes_are_single_property_community_safe() -> None:
    """Community Edition supports plain single-property indexes only — no
    composite constraint / NODE KEY for the tenant dimension here."""
    driver = _RecordingDriver()
    m0005_tenant_scoping.run(driver)
    for stmt in driver.statements:
        if "CREATE INDEX" in stmt:
            assert "NODE KEY" not in stmt
            assert "CONSTRAINT" not in stmt
            assert "(n.tenant_id)" in stmt  # single property


def test_m0005_excludes_global_and_identity_labels() -> None:
    """Domain (global taxonomy) and User/Tenant (identity) must NOT be
    stamped as per-tenant content."""
    for excluded in ("Domain", "User", "Tenant"):
        assert excluded not in _LABELS


def test_m0005_registered_after_m0004() -> None:
    from scripts.run_migrations import MIGRATIONS

    assert "app.db.neo4j.migrations.m0005_tenant_scoping" in MIGRATIONS
    assert MIGRATIONS.index(
        "app.db.neo4j.migrations.m0005_tenant_scoping",
    ) == MIGRATIONS.index("app.db.neo4j.migrations.m0004_fact_nodes") + 1
