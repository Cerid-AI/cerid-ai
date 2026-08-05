# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0006: Bi-temporal indexes for (:Fact) — the four-timestamp contract.

Idempotent. Safe to re-run. SCHEMA ONLY — no data backfill, no new
constraints, no properties written. m0004's ``:Fact`` scaffolding still
has zero writers (nothing extracts or MERGEs a ``:Fact`` node yet); this
migration only adds indexes for properties the future writer (bi-temporal
memory plan Phase C) will set.

Background (docs/superpowers/plans/2026-07-15-bitemporal-memory-plan.md
§Phase B, Open Decision D2 — ratified by the owner): m0004's
``invalid_at`` is system-time expiry ("when we stopped believing the
fact"), but its docstring left the *valid-time* dimension ("when the
fact stopped being true in the world") unaddressed. Collapsing the two
onto one timestamp loses "as-of" queries — a fact invalidated today
because a contradiction was just detected can have stopped being true
weeks ago. D2 ratifies the full four-timestamp (Graphiti) model:

CANONICAL FOUR-TIMESTAMP CONTRACT — the reference every later phase of
the bi-temporal plan (C/D/E/F) cites for what these properties mean:

    created_at  — SYSTEM time. When this :Fact node was written.
                  Immutable once set. (m0004; unchanged.)
    invalid_at  — SYSTEM time. When CODE stopped believing the fact
                  (superseded by a contradicting fact, or otherwise
                  invalidated). NULL while the fact is the current
                  belief. (m0004; unchanged — NOT renamed.) Mirrors
                  core/agents/memory_consolidation.py mark_superseded:
                  code sets this, never the LLM.
    valid_from  — WORLD/valid time. When the fact BECAME true. Seeded
                  from the source memory's ``event_date`` (or ingestion
                  time when no event_date is known). (NEW, m0006.)
    valid_to    — WORLD/valid time. When the fact STOPPED being true.
                  NULL means "still true as far as we know". (NEW,
                  m0006.)

``invalid_at`` and ``valid_to`` are DISTINCT axes, not aliases: a fact
can be invalidated (belief revised) long after it stopped being valid
in the world, and for the EVENT-vs-STATE split m0004 already encodes in
``fact_key`` — an EVENT fact's ``valid_to`` may equal its ``valid_from``
(a point-in-time occurrence), while a STATE fact's ``valid_to`` moves
forward each time belief is revised.

Query semantics (Phase F wires these; documented here so the contract
is stable before any reader depends on it):
  * "current belief, currently true" — invalid_at IS NULL AND
    valid_to IS NULL
  * "as-of T" (what did we believe about the world at T)     —
    valid_from <= T AND (valid_to IS NULL OR valid_to > T)
  * CODE sets all four timestamps; the LLM/extractor never writes them
    directly (mirrors the m0004 invalid_at rule).

This migration creates NO constraints — m0004 already owns
``fact_uid_unique`` and the dedup contract on it is unchanged — and
writes NO properties. It is pure index scaffolding for a writer that
does not exist yet.

Index decision — composite vs single-property (verified against the
deployed image, ``neo4j:2026.04.0-community``, docker-compose.yml:42):
Neo4j Community Edition supports composite *range* indexes on nodes
(``CREATE INDEX ... FOR (n:Label) ON (n.a, n.b)``) — that is not
Enterprise-gated; only composite/NODE-KEY *constraints* are (m0004's
own precedent above, re-affirmed here). So a composite
``(subject_id, valid_to)`` index is technically available. But no
composite index of any kind exists anywhere in this codebase today —
``schema.py`` and m0001-m0005 are all single-property indexes and
constraints. Introducing the first composite index in a schema-only
migration with no writer yet to validate real query plans against is
exactly the unproven-pattern risk worth avoiding here. The honest
choice: ``fact_valid_to_idx`` below is single-property (mirrors
m0004's ``fact_invalid_at_idx``), and combined with m0004's existing
``fact_subject_idx`` (already on ``subject_id``), the query planner can
serve the ``(subject_id, valid_to)`` access pattern via index
intersection over the two single-property indexes. If profiling after
Phase C's writer lands shows intersection isn't sufficient, promote to
a true composite index then, backed by real query plans.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0006")

# valid_to index: the world-time counterpart to m0004's
# fact_invalid_at_idx. Serves "is this fact still true" (`valid_to IS
# NULL`) and "as-of T" (`valid_to > $t`) queries. Paired with m0004's
# fact_subject_idx (subject_id), the two single-property indexes serve
# the (subject_id, valid_to) access pattern together — see the
# docstring "Index decision" above for why this is a deliberate
# composite-index fallback, not an oversight.
_FACT_VALID_TO_INDEX = """
CREATE INDEX fact_valid_to_idx IF NOT EXISTS
FOR (f:Fact) ON (f.valid_to)
"""


def run(driver) -> dict[str, int]:
    """Apply the migration. Returns the count of indexes created or
    already-present (Neo4j IF NOT EXISTS makes both paths look
    identical from the client side)."""
    created = 0
    with driver.session() as session:
        for cypher in (_FACT_VALID_TO_INDEX,):
            session.run(cypher)
            created += 1
    logger.info("m0006: created/verified %d Fact bi-temporal schema objects", created)
    return {"schema_objects": created}
