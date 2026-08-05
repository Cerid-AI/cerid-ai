# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Read-side queries for the bi-temporal :Fact layer (m0004/m0006).

Bi-temporal memory plan Phase F (F1) — the first :Fact *reader* (the writer is
``app/db/neo4j/facts.py``; the interval-closure writer is
``core/agents/fact_invalidation.py``). Every query here implements the CANONICAL
four-timestamp query semantics documented in the m0006 migration docstring
(``app/db/neo4j/migrations/m0006_fact_bitemporal.py``):

    current belief, currently true — invalid_at IS NULL AND valid_to IS NULL
    as-of T (what we believed about the world at T)
                                   — valid_from <= T AND (valid_to IS NULL OR valid_to > T)

``invalid_at`` and ``valid_to`` are DISTINCT axes: ``invalid_at`` is SYSTEM time
(when code stopped believing the fact), ``valid_to`` is WORLD/valid time (when
the fact stopped being true). "Current" gates on both being open; "as-of" is a
pure valid-time question and deliberately ignores ``invalid_at`` (a fact
invalidated today can still be the correct answer to "what was true last month").

Timestamp shape, consistent with the writer (``app/db/neo4j/facts.py``): on
Neo4j ``valid_to``/``invalid_at`` are real ``NULL`` when open/active (the Chroma
mirror uses the empty-string ``OPEN_INTERVAL`` sentinel because Chroma metadata
is str-only — same semantics, each store's own type system). ``valid_from`` is a
string that may be empty when neither an event date nor an observation date was
known (``resolve_valid_from``); an empty/absent ``valid_from`` is treated as "no
lower bound" for the as-of predicate, matching the derivation layer's
``bitemporal`` fixtures. Reads never mutate — no chunking is needed (that guards
the writer's transaction size, not a read).

R5 (verification-source inadmissibility): a fact whose ``source`` is
``'verification'`` was derived from a verification-promoted memory and must stay
inadmissible *as verification evidence*. The ``include_verification_sourced``
flag exists so the verification path can pass ``False`` to exclude those facts;
ordinary consumers keep the default ``True``.

Index posture (EXPLAIN-friendly, single-property indexes only — m0004/m0006):
every query seeks by ``subject_id`` (``fact_subject_idx``) and filters on
``valid_to`` (``fact_valid_to_idx``) / ``invalid_at`` (``fact_invalid_at_idx``).
No composite index is assumed (none exists yet — m0006 docstring "Index
decision"); the planner serves the ``(subject_id, valid_to)`` access pattern by
intersecting the single-property indexes.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.graph.fact_queries")

# The provenance flag marking a verification-derived fact (mirrors
# core.agents.fact_derivation.FACT_SOURCE_VERIFICATION — kept as a local literal
# so this read module carries no dependency on the derivation layer). Excluded
# when a caller passes include_verification_sourced=False (plan R5).
_VERIFICATION_SOURCE = "verification"

# The full :Fact property projection every row reader returns. Named once so the
# two readers stay in lockstep and the RETURN clause reads the same in EXPLAIN.
_FACT_RETURN = """
RETURN f.uid AS uid, f.subject_id AS subject_id, f.object_id AS object_id,
       f.predicate AS predicate, f.fact_key AS fact_key, f.event_date AS event_date,
       f.valid_from AS valid_from, f.valid_to AS valid_to, f.invalid_at AS invalid_at,
       f.created_at AS created_at, f.source AS source
ORDER BY f.valid_from, f.uid
"""

# WHERE fragment: "current belief, currently true" (m0006). Both axes open.
_CURRENT_WHERE = "f.invalid_at IS NULL AND f.valid_to IS NULL"

# WHERE fragment: the m0006 "as-of T" predicate. Pure valid-time — no invalid_at.
# Empty/absent valid_from = no lower bound (matches the derivation fixtures);
# NULL valid_to = still true (open interval).
_AS_OF_WHERE = (
    "(f.valid_from IS NULL OR f.valid_from = '' OR f.valid_from <= $t) "
    "AND (f.valid_to IS NULL OR f.valid_to > $t)"
)

# WHERE fragment appended when a predicate is supplied.
_PREDICATE_CLAUSE = "AND f.predicate = $predicate"

# WHERE fragment appended when verification-sourced facts must be excluded (R5).
# coalesce so a NULL source (not verification-derived) is admitted, not dropped.
_EXCLUDE_VERIFICATION_CLAUSE = "AND coalesce(f.source, '') <> $verification_source"


def _row_where(
    base: str, *, predicate: str | None, include_verification_sourced: bool
) -> str:
    """Assemble the WHERE body from the base temporal predicate plus the optional
    predicate / verification-exclusion clauses. The fragments are code-controlled
    constants (never user input); ``subject_id``/``t``/``predicate`` flow as
    query parameters."""
    parts = [base]
    if predicate:
        parts.append(_PREDICATE_CLAUSE)
    if not include_verification_sourced:
        parts.append(_EXCLUDE_VERIFICATION_CLAUSE)
    return "\n  ".join(parts)


def _run_rows(driver, cypher: str, params: dict[str, Any]) -> list[dict]:
    """Execute a row-returning reader and materialise the records as dicts."""
    with driver.session() as session:
        result = session.run(cypher, **params)
        return [dict(record) for record in result]


def _run_count(driver, cypher: str, params: dict[str, Any]) -> int:
    """Execute a count reader and return the scalar (0 when the row is absent)."""
    with driver.session() as session:
        row = session.run(cypher, **params).single()
    if row is None or row["n"] is None:
        return 0
    return int(row["n"])


def current_facts(
    driver,
    subject_id: str,
    *,
    predicate: str | None = None,
    include_verification_sourced: bool = True,
) -> list[dict]:
    """Facts about ``subject_id`` that are the current belief and currently true.

    Implements the m0006 "current" predicate (``invalid_at IS NULL AND valid_to
    IS NULL``). Optionally scoped to one ``predicate`` (the memory-type slug the
    Phase-C derivation writes). Pass ``include_verification_sourced=False`` to
    drop ``source='verification'`` facts (R5). Returns one dict per fact (empty
    list on an empty graph). Read-only.
    """
    params: dict[str, Any] = {"subject_id": subject_id}
    if predicate:
        params["predicate"] = predicate
    if not include_verification_sourced:
        params["verification_source"] = _VERIFICATION_SOURCE
    where = _row_where(
        _CURRENT_WHERE,
        predicate=predicate,
        include_verification_sourced=include_verification_sourced,
    )
    cypher = f"MATCH (f:Fact {{subject_id: $subject_id}})\nWHERE {where}\n{_FACT_RETURN}"
    return _run_rows(driver, cypher, params)


def facts_as_of(
    driver,
    subject_id: str,
    t: str,
    *,
    predicate: str | None = None,
    include_verification_sourced: bool = True,
) -> list[dict]:
    """Facts about ``subject_id`` admissible AS-OF valid time ``t`` (m0006).

    Implements the m0006 "as-of T" predicate (``valid_from <= T AND (valid_to IS
    NULL OR valid_to > T)``) — a pure valid-time query, so a fact whose belief
    was later revised (``invalid_at`` set) is STILL returned if ``t`` falls inside
    its ``[valid_from, valid_to)`` interval. An empty/absent ``valid_from`` is a
    fact with no known start and is admitted at any ``t`` (no lower bound). Same
    ``predicate`` / verification-exclusion options as :func:`current_facts`.
    """
    params: dict[str, Any] = {"subject_id": subject_id, "t": t}
    if predicate:
        params["predicate"] = predicate
    if not include_verification_sourced:
        params["verification_source"] = _VERIFICATION_SOURCE
    where = _row_where(
        _AS_OF_WHERE,
        predicate=predicate,
        include_verification_sourced=include_verification_sourced,
    )
    cypher = f"MATCH (f:Fact {{subject_id: $subject_id}})\nWHERE {where}\n{_FACT_RETURN}"
    return _run_rows(driver, cypher, params)


def count_facts(
    driver,
    subject_id: str,
    predicate: str | None,
    *,
    as_of: str | None = None,
    distinct: bool = True,
) -> int:
    """Symbolic ``count(DISTINCT f)`` of ``subject_id``'s facts (m0006).

    The payoff of the uid MERGE identity: because a re-extracted fact collapses
    onto one node (``uid = "{subject_id}|{fact_key}"``), the node count is a
    *symbolic* count, not an LLM-trusted one. For EVENT facts, ``fact_key``
    embeds ``event_date``, so N occurrences on N distinct dates are N distinct
    nodes — ``count(DISTINCT f)`` is the occurrence count. For STATE facts the
    count is the number that satisfy the interval predicate.

    ``predicate=None`` counts across all predicates for the subject; a slug
    scopes to one memory-type. ``as_of=None`` counts CURRENT facts (``invalid_at
    IS NULL AND valid_to IS NULL``); ``as_of=T`` counts facts admissible as-of
    valid time ``T``. Returns 0 on an empty graph.
    """
    params: dict[str, Any] = {"subject_id": subject_id}
    if as_of is None:
        base = _CURRENT_WHERE
    else:
        base = _AS_OF_WHERE
        params["t"] = as_of
    parts = [base]
    if predicate:
        parts.append(_PREDICATE_CLAUSE)
        params["predicate"] = predicate
    where = "\n  ".join(parts)
    agg = "count(DISTINCT f)" if distinct else "count(f)"
    cypher = (
        f"MATCH (f:Fact {{subject_id: $subject_id}})\nWHERE {where}\nRETURN {agg} AS n"
    )
    return _run_count(driver, cypher, params)


def subjects_with_current_facts(driver, subject_ids: list[str]) -> set[str]:
    """Of ``subject_ids``, the subset that hold >= 1 current fact (m0006).

    A deterministic, index-friendly (``fact_subject_idx`` over the ``IN`` list)
    disambiguation probe: given candidate canonical ids, return only those the
    graph actually has current facts for. The symbolic-count seam
    (``app/mcp_tools/retrieval.py``) uses "exactly one match" as its resolution
    gate — zero or many means fall through to the text path. Empty input or an
    empty graph returns an empty set.
    """
    if not subject_ids:
        return set()
    cypher = (
        "MATCH (f:Fact)\n"
        "WHERE f.subject_id IN $subject_ids\n"
        f"  AND {_CURRENT_WHERE}\n"
        "RETURN DISTINCT f.subject_id AS subject_id"
    )
    with driver.session() as session:
        result = session.run(cypher, subject_ids=list(subject_ids))
        return {record["subject_id"] for record in result}
