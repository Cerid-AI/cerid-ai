# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0006 bi-temporal :Fact indexes — DDL contract + registration.

Schema-only migration: guards the index shape (valid_to single-property,
no new constraints, no composite index) and its registration in the
migration runner. No live Neo4j — the driver is mocked, mirroring the
m0004 test idiom (tests/test_migration_m0004_fact_nodes.py).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from app.db.neo4j.migrations import m0006_fact_bitemporal


class _RecordingDriver:
    """Minimal Neo4j driver stub that records every Cypher statement run."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextmanager
    def session(self):
        sess = MagicMock()
        sess.run.side_effect = lambda cypher, **kw: self.statements.append(cypher)
        yield sess


def test_m0006_issues_valid_to_index() -> None:
    driver = _RecordingDriver()
    result = m0006_fact_bitemporal.run(driver)

    assert result == {"schema_objects": 1}
    joined = "\n".join(driver.statements)
    assert "INDEX fact_valid_to_idx" in joined
    assert joined.count("FOR (f:Fact)") == 1


def test_m0006_is_idempotent_if_not_exists() -> None:
    """Every statement must be IF NOT EXISTS so re-running is a no-op."""
    driver = _RecordingDriver()
    m0006_fact_bitemporal.run(driver)
    for stmt in driver.statements:
        assert "IF NOT EXISTS" in stmt


def test_m0006_creates_no_new_constraints() -> None:
    """m0006 is index-only — m0004 already owns fact_uid_unique; no
    backfill, no new uniqueness contract."""
    driver = _RecordingDriver()
    m0006_fact_bitemporal.run(driver)
    joined = "\n".join(driver.statements)
    assert "CONSTRAINT" not in joined


def test_m0006_uses_no_composite_index() -> None:
    """Per the plan's honest-fallback rule: no composite index exists
    anywhere in this codebase yet, so m0006 stays single-property and
    relies on m0004's fact_subject_idx + this valid_to index together."""
    driver = _RecordingDriver()
    m0006_fact_bitemporal.run(driver)
    for stmt in driver.statements:
        # A composite index's ON clause would contain a comma.
        on_clause = stmt.split("ON (")[1].split(")")[0] if "ON (" in stmt else ""
        assert "," not in on_clause


def test_m0006_registered_in_migration_runner() -> None:
    from scripts.run_migrations import MIGRATIONS

    assert "app.db.neo4j.migrations.m0006_fact_bitemporal" in MIGRATIONS
    # Ordering matters — m0006 runs after m0005.
    assert MIGRATIONS.index(
        "app.db.neo4j.migrations.m0006_fact_bitemporal",
    ) == MIGRATIONS.index("app.db.neo4j.migrations.m0005_tenant_scoping") + 1
