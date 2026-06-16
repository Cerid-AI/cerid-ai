# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""m0004 (:Fact) schema-scaffolding migration — DDL contract + registration.

Schema scaffolding only: this guards the migration's DDL shape and its
Community-Edition safety (single-property uniqueness, since composite /
NODE KEY constraints are Enterprise-only on neo4j:*-community). No live
Neo4j — the driver is mocked.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from app.db.neo4j.migrations import m0004_fact_nodes


class _RecordingDriver:
    """Minimal Neo4j driver stub that records every Cypher statement run."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextmanager
    def session(self):
        sess = MagicMock()
        sess.run.side_effect = lambda cypher, **kw: self.statements.append(cypher)
        yield sess


def test_m0004_issues_constraint_and_indexes() -> None:
    driver = _RecordingDriver()
    result = m0004_fact_nodes.run(driver)

    assert result == {"schema_objects": 3}
    joined = "\n".join(driver.statements)
    # One uniqueness constraint + two indexes, all on the :Fact label.
    assert "CONSTRAINT fact_uid_unique" in joined
    assert "INDEX fact_subject_idx" in joined
    assert "INDEX fact_invalid_at_idx" in joined
    assert joined.count("FOR (f:Fact)") == 3


def test_m0004_is_idempotent_if_not_exists() -> None:
    """Every statement must be IF NOT EXISTS so re-running is a no-op."""
    driver = _RecordingDriver()
    m0004_fact_nodes.run(driver)
    for stmt in driver.statements:
        assert "IF NOT EXISTS" in stmt


def test_m0004_uniqueness_is_single_property_community_safe() -> None:
    """Community Edition has no composite/NODE KEY constraints — the dedup
    key must be the single derived `uid` property, not a (a, b) tuple."""
    driver = _RecordingDriver()
    m0004_fact_nodes.run(driver)
    constraint = next(s for s in driver.statements if "CONSTRAINT" in s)
    assert "REQUIRE f.uid IS UNIQUE" in constraint
    assert "NODE KEY" not in constraint
    assert "," not in constraint.split("REQUIRE")[1]  # no composite tuple


def test_m0004_registered_in_migration_runner() -> None:
    from scripts.run_migrations import MIGRATIONS

    assert "app.db.neo4j.migrations.m0004_fact_nodes" in MIGRATIONS
    # Ordering matters — m0004 runs after m0003.
    assert MIGRATIONS.index(
        "app.db.neo4j.migrations.m0004_fact_nodes",
    ) == MIGRATIONS.index("app.db.neo4j.migrations.m0003_source_nodes") + 1
