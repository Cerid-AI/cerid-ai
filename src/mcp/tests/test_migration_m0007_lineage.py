# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0007 lineage indexes: the lookups the lineage writer and fact closure make,
idempotent, and registered with the migration runner."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from app.db.neo4j.migrations import m0007_lineage


class _RecordingDriver:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextmanager
    def session(self):
        sess = MagicMock()
        sess.run.side_effect = lambda cypher, **kw: self.statements.append(cypher)
        yield sess


def test_m0007_indexes_what_the_writer_looks_up() -> None:
    driver = _RecordingDriver()
    assert m0007_lineage.run(driver) == {"schema_objects": 4}
    joined = "\n".join(driver.statements)
    for needle in ("(a:Artifact) ON (a.lineage_id)", "(m:Memory) ON (m.lineage_id)",
                   "(a:Artifact) ON (a.superseded_by)", "(f:Fact) ON (f.source_artifact_id)"):
        assert needle in joined
    assert all("IF NOT EXISTS" in s for s in driver.statements)


def test_m0007_is_registered_with_the_runner() -> None:
    from scripts.run_migrations import MIGRATIONS

    assert MIGRATIONS[-1] == "app.db.neo4j.migrations.m0007_lineage"
