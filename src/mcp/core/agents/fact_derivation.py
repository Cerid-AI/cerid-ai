# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure derivation of bi-temporal :Fact tuples from an extracted memory.

Bi-temporal memory plan Phase C (C1). Store-free and LLM-free: turns an
already-extracted memory (``content`` + ``memory_type`` + ``event_date``) plus
its already-resolved entity mentions (canonical ids from the entity-extraction
job) into ``(subject, predicate, object, fact_key, valid_from)`` tuples that the
Neo4j writer (``app.db.neo4j.facts``) MERGEs as ``(:Fact)`` nodes.

Cost model: **zero marginal LLM calls per memory / per session.** The
entity-extraction job already makes exactly one LLM call (entity extraction)
whose output this module consumes; deriving facts adds only in-process CPU work
(O(entities), bounded by :data:`MAX_FACTS_PER_MEMORY`). This honours the plan's
Risk-R3 mitigation ("derive facts from the existing entity-extraction job where
possible; avoid a second LLM pass") — verified necessary because the entity
extractor's LLM output carries no predicates/relationships, only a flat entity
list, so there is nothing richer to reuse and a second call is deliberately not
made.

STATE-vs-EVENT contract (mirrors ``tests/eval/longmemeval/bitemporal.py`` and
``app/db/neo4j/migrations/m0004_fact_nodes.py``):

- STATE facts (memory types in :data:`config.settings.MEMORY_POWER_LAW_TYPES` —
  empirical/decision/preference) describe a current-state attribute a newer
  value supersedes; ``event_date`` is EXCLUDED from ``fact_key`` so one subject
  keeps one state fact (interval closure — Phase D — later revises belief).
- EVENT facts (every other type — temporal/project_context/conversational,
  dated occurrences) coexist; ``event_date`` PARTICIPATES in ``fact_key`` so N
  occurrences on N distinct dates are N distinct facts (``count(DISTINCT)``).

Predicate: the deterministic Phase-C predicate is the ``memory_type`` slug (a
controlled ``^[a-z_]+$`` vocabulary). The facts are unary (no object entity) —
richer binary subject-predicate-object triples need an LLM verb the entity job
does not produce, so they are deferred; the writer is already binary-capable
(FACT_OBJECT) so a later LLM-backed derivation needs no writer rework.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from config.settings import MEMORY_POWER_LAW_TYPES

# STATE memory types supersede on a new value (event_date excluded from the key);
# every other type is an EVENT that coexists (event_date in the key). Mirrors
# bitemporal.py STATE_MEMORY_TYPES exactly so the graph and Chroma stores agree.
_STATE_MEMORY_TYPES: frozenset[str] = frozenset(MEMORY_POWER_LAW_TYPES)

# Upper bound on facts derived from a single memory (Risk R2 — write
# amplification). A memory that resolves to more than this many distinct
# entities is pathological; the excess is dropped after a deterministic sort so
# the truncation is stable across re-extraction (idempotent MERGE).
MAX_FACTS_PER_MEMORY = 32

# Provenance source flags carried onto the :Fact (Risk R5 — a
# verification-derived fact must stay inadmissible as verification evidence, so
# it must be distinguishable from an ordinary extraction-derived fact).
FACT_SOURCE_VERIFICATION = "verification"
FACT_SOURCE_EXTRACTION = "extraction"

# The fact_key field separator (mirrors m0004's uid "{subject_id}|{fact_key}").
_KEY_SEP = "|"

#: ``valid_to`` open-interval sentinel for the CHROMA memory store ("still
#: true"). Chroma metadata is str-only — ``None`` is not storable — so the empty
#: string is the marker, mirroring ``bitemporal.py::OPEN_INTERVAL``. The Neo4j
#: :Fact writer uses a real ``null`` for the same meaning (each store's own type
#: system; identical semantics).
OPEN_INTERVAL = ""

# Sentinel a source memory sets when it was promoted by verification
# (verified_memory.py writes ``memory_source_type = "verification"`` into the
# Chroma chunk metadata).
_VERIFICATION_SOURCE_MARKER = "verification"


@dataclass(frozen=True)
class DerivedFact:
    """One bi-temporal fact ready for the Neo4j writer.

    ``object_id`` is ``None`` for a unary fact (the Phase-C deterministic case).
    ``valid_from`` is the world/valid-time start (event_date, else observation
    date); ``valid_to`` / ``invalid_at`` are set by the writer (open/active).
    """

    subject_id: str
    predicate: str
    object_id: str | None
    fact_key: str
    valid_from: str
    event_date: str
    is_state: bool
    source: str


def _clean_date(value: str | None) -> str:
    """Normalise a raw date string: stripped, with the literal ``"null"`` (the
    extractor's no-date marker) treated as absent."""
    text = (value or "").strip()
    if not text or text.lower() == "null":
        return ""
    return text


def resolve_valid_from(*, event_date: str | None, observation_date: str | None) -> str:
    """Valid-time start: the fact's ``event_date`` when present, else the
    observation/session date (the ambiguity default). Empty when neither is
    known. Mirrors ``tests/eval/longmemeval/bitemporal.py::resolve_valid_from``
    so the graph and Chroma stores resolve identical intervals."""
    event = _clean_date(event_date)
    if event:
        return event
    return _clean_date(observation_date)


def is_state_memory_type(memory_type: str) -> bool:
    """True when ``memory_type`` is a supersedable STATE type (excludes
    event_date from the fact key); False for EVENT types (includes it)."""
    return memory_type in _STATE_MEMORY_TYPES


def build_fact_key(
    predicate: str, object_id: str | None, event_date: str, *, is_state: bool
) -> str:
    """Dedup key: ``predicate`` (+ ``|object_id`` when binary), PLUS
    ``|event_date`` for EVENT facts and WITHOUT it for STATE facts. The
    EVENT-vs-STATE off-by-one of counting lives entirely in this key (m0004)."""
    parts = [predicate]
    if object_id:
        parts.append(object_id)
    if not is_state and event_date:
        parts.append(event_date)
    return _KEY_SEP.join(parts)


def fact_uid(subject_id: str, fact_key: str) -> str:
    """Graph-level MERGE key ``"{subject_id}|{fact_key}"`` (m0004's
    single-property Community-Edition dedup identity)."""
    return f"{subject_id}{_KEY_SEP}{fact_key}"


def resolve_fact_source(memory_source_type: str | None) -> str:
    """Provenance flag for the derived fact: ``verification`` when the source
    memory was verification-promoted, else ``extraction`` (Risk R5)."""
    if (memory_source_type or "").strip().lower() == _VERIFICATION_SOURCE_MARKER:
        return FACT_SOURCE_VERIFICATION
    return FACT_SOURCE_EXTRACTION


def derive_facts(
    *,
    content: str,
    memory_type: str,
    event_date: str | None,
    observation_date: str | None,
    entity_ids: Sequence[str],
    memory_source_type: str | None = None,
) -> list[DerivedFact]:
    """Derive unary :Fact tuples for one extracted memory.

    One fact per resolved entity mention (deduped, deterministically ordered,
    capped at :data:`MAX_FACTS_PER_MEMORY`). Empty memory content or no
    resolved entities yields no facts. Deterministic and free of any store or
    LLM dependency — the temporal-consistency fixtures exercise it directly.
    """
    if not content.strip() or not entity_ids:
        return []

    is_state = is_state_memory_type(memory_type)
    predicate = memory_type
    ev = _clean_date(event_date)
    valid_from = resolve_valid_from(
        event_date=event_date, observation_date=observation_date
    )
    source = resolve_fact_source(memory_source_type)

    # Deduplicate + deterministically order the subjects so the cap and the
    # resulting uids are stable across re-extraction (idempotent MERGE).
    subjects = sorted({e for e in entity_ids if e})[:MAX_FACTS_PER_MEMORY]

    facts: list[DerivedFact] = []
    for subject_id in subjects:
        fact_key = build_fact_key(predicate, None, ev, is_state=is_state)
        facts.append(
            DerivedFact(
                subject_id=subject_id,
                predicate=predicate,
                object_id=None,
                fact_key=fact_key,
                valid_from=valid_from,
                event_date=ev,
                is_state=is_state,
                source=source,
            )
        )
    return facts
