# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0004: Introduce (:Fact) nodes for graph-backed counting/aggregation.

Idempotent. Safe to re-run. SCHEMA SCAFFOLDING ONLY — this migration
creates constraints + indexes; it does NOT extract or backfill any
:Fact nodes, and nothing writes them yet.

Background (tasks/2026-06-16-phase3-structural-memory-design.md):
the roadmap's Phase-3 structural-memory item wants an entity-centric
fact layer so "how many X" questions resolve to a symbolic
`count(DISTINCT)` over the graph instead of an LLM counting items in
retrieved text. The exploration settled two design points this schema
encodes:

  1. NODE model, not relationship model. Neo4j 5 cannot index or
     constrain *relationship* properties, so `[:FACT {fact_key}]` could
     not be deduped or counted efficiently. A (:Fact) node can.
  2. Community-Edition-safe dedup. Composite uniqueness / NODE KEY
     constraints are Enterprise-only, and this deployment runs
     `neo4j:*-community`. So graph-level dedup is enforced on a single
     derived property `uid = "{subject_id}|{fact_key}"` with an
     ordinary single-property uniqueness constraint. A writer MERGEs on
     `uid`, which makes a re-extracted fact collapse to one node — the
     count is then symbolic (`count(DISTINCT f)`), not LLM-trusted.

Intended (NOT created here — deferred with the fact-extraction agent):
  (:Entity {canonical_id})-[:HAS_FACT]->(:Fact)-[:FACT_OBJECT]->(:Entity)

Intended :Fact properties (the writer's contract):
  uid         — "{subject_id}|{fact_key}" (unique; MERGE key)
  subject_id  — canonical_id of the subject entity
  object_id   — canonical_id of the object entity (nullable; unary facts)
  predicate   — relation verb/slug (e.g. "attended", "lives_in")
  fact_key    — dedup key: predicate + normalised object, PLUS event_date
                for EVENT-type facts (two yoga classes on different dates
                are two facts) and WITHOUT it for STATE facts (one
                "lives in Denver"). The off-by-one of counting is the
                EVENT-vs-STATE distinction in this key, set in code.
  event_date  — ISO-8601 date the fact is ABOUT (nullable; EVENT facts)
  invalid_at  — ISO-8601 timestamp the fact was superseded, or null while
                active (bi-temporal; CODE sets it, never the LLM —
                mirrors core/agents/memory_consolidation.py mark_superseded).
  created_at  — ISO-8601 write time.

Preservation invariant for the future writer (add as a /health check
when extraction lands, mirroring the m0002 verification-orphan gate):
every (:Fact) must have an incoming (:Entity)-[:HAS_FACT]-> edge — zero
orphan :Fact nodes.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0004")

# Single-property uniqueness on the derived composite key. Community
# Edition supports single-property uniqueness only (no NODE KEY / no
# composite uniqueness), so the (subject_id, fact_key) pair is folded
# into `uid` and constrained here. The writer MERGEs on `uid` for
# graph-level dedup → symbolic counts.
_FACT_UID_CONSTRAINT = """
CREATE CONSTRAINT fact_uid_unique IF NOT EXISTS
FOR (f:Fact) REQUIRE f.uid IS UNIQUE
"""

# Subject index: the primary access path ("facts about entity X") and
# the grouping key for `count(DISTINCT f)` aggregations.
_FACT_SUBJECT_INDEX = """
CREATE INDEX fact_subject_idx IF NOT EXISTS
FOR (f:Fact) ON (f.subject_id)
"""

# invalid_at index: read-time active-fact filter
# (`WHERE f.invalid_at IS NULL`), mirroring the supersession read filter
# on :Artifact. Gated in app code by a future ENABLE_FACT_INVALIDATION_FILTER.
_FACT_INVALID_AT_INDEX = """
CREATE INDEX fact_invalid_at_idx IF NOT EXISTS
FOR (f:Fact) ON (f.invalid_at)
"""


def run(driver) -> dict[str, int]:
    """Apply the migration. Returns the count of constraints + indexes
    created or already-present (Neo4j IF NOT EXISTS makes both paths look
    identical from the client side)."""
    created = 0
    with driver.session() as session:
        for cypher in (
            _FACT_UID_CONSTRAINT,
            _FACT_SUBJECT_INDEX,
            _FACT_INVALID_AT_INDEX,
        ):
            session.run(cypher)
            created += 1
    logger.info("m0004: created/verified %d Fact schema objects", created)
    return {"schema_objects": created}
