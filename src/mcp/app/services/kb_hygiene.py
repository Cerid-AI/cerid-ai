# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""KB hygiene sweeps — purge test residue from the production stores.

Live-stack test runs (preservation probes, e2e drives) ingest real
artifacts and clean up on teardown; a crashed run leaves its markers
behind as if they were user knowledge (UX-14/20). The sweep removes
everything in the test-residue namespace (``core.utils.test_residue``)
from Neo4j + Chroma + the lexical indexes, both artifacts and the
entity nodes their extraction created.

Callers: the manual admin endpoint (``POST /admin/kb/purge-test-residue``)
and the weekly scheduler sweep — same function, so the guard that is
proven in tests is the one that runs in production.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from core.utils.swallowed import log_swallowed_error
from core.utils.test_residue import (
    TEST_RESIDUE_EXACT_NAMES,
    TEST_RESIDUE_MEMORY_CONVO_PREFIXES,
    TEST_RESIDUE_PREFIXES,
    TEST_RESIDUE_TEXT_MARKERS,
    is_test_residue_name,
    is_test_residue_text,
)
from core.utils.time import utcnow

logger = logging.getLogger("ai-companion.kb-hygiene")

# A live-stack test run's probes must not be yanked out from under it while
# the run is still executing: only residue older than this is purged.
GRACE_PERIOD = timedelta(hours=1)

_SAMPLE_LIMIT = 30


def _candidate_where_clause(var: str, name_prop: str) -> str:
    """Cypher prefilter — a SUPERSET of the namespace; Python re-checks.

    Built from the same module constants the Python matcher uses so the
    two cannot drift: Cypher narrows the scan, ``is_test_residue_name``
    decides.
    """
    clauses = [
        f"{var}.{name_prop} STARTS WITH '{p}'" for p in TEST_RESIDUE_PREFIXES
    ]
    clauses.extend(
        f"({var}.{name_prop} STARTS WITH 'memory_' "
        f"AND {var}.{name_prop} CONTAINS '_{c}')"
        for c in TEST_RESIDUE_MEMORY_CONVO_PREFIXES
    )
    clauses.append(f"{var}.{name_prop} IN $exact_names")
    return " OR ".join(clauses)


def sweep_test_residue(
    neo4j: Any,
    chroma: Any | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Find (and with ``apply=True`` delete) all test-residue KB content.

    Returns ``{"artifacts_found", "artifacts_purged", "entities_found",
    "entities_purged", "skipped_in_grace", "samples"}``. Dry-run
    (``apply=False``) reports without deleting.
    """
    cutoff_iso = (utcnow() - GRACE_PERIOD).isoformat()

    # Probe suites ingest content WITHOUT a filename (stored as text_input),
    # so the marker only exists in the summary — the artifact arm must also
    # scan summaries or it is structurally blind to the observed residue.
    summary_clauses = " OR ".join(
        f"a.summary CONTAINS '{m}'" for m in TEST_RESIDUE_TEXT_MARKERS
    )
    artifact_query = (
        "MATCH (a:Artifact) "
        f"WHERE {_candidate_where_clause('a', 'filename')} "
        f"OR {summary_clauses} "
        "RETURN a.id AS id, a.filename AS filename, "
        "coalesce(a.summary, '') AS summary, "
        "a.ingested_at AS ingested_at"
    )
    entity_query = (
        "MATCH (e:Entity) "
        f"WHERE {_candidate_where_clause('e', 'name')} "
        "RETURN e.canonical_id AS canonical_id, e.name AS name, "
        "e.updated_at AS updated_at"
    )
    params = {"exact_names": list(TEST_RESIDUE_EXACT_NAMES)}

    with neo4j.session() as session:
        artifact_rows = [dict(r) for r in session.run(artifact_query, **params)]
        entity_rows = [dict(r) for r in session.run(entity_query, **params)]

    skipped_in_grace = 0
    samples: list[str] = []

    residue_artifacts: list[dict[str, Any]] = []
    for row in artifact_rows:
        if not (
            is_test_residue_name(str(row.get("filename") or ""))
            or is_test_residue_text(str(row.get("summary") or ""))
        ):
            continue
        if str(row.get("ingested_at") or "") >= cutoff_iso:
            skipped_in_grace += 1
            continue
        residue_artifacts.append(row)
        if len(samples) < _SAMPLE_LIMIT:
            samples.append(str(row.get("filename")))

    residue_entities: list[dict[str, Any]] = []
    for row in entity_rows:
        if not is_test_residue_name(str(row.get("name") or "")):
            continue
        # Missing updated_at = old node = eligible.
        if str(row.get("updated_at") or "") >= cutoff_iso:
            skipped_in_grace += 1
            continue
        residue_entities.append(row)
        if len(samples) < _SAMPLE_LIMIT:
            samples.append(str(row.get("name")))

    artifacts_purged = 0
    entities_purged = 0
    if apply:
        from app.services.content_lifecycle import remove_content

        for row in residue_artifacts:
            try:
                result = remove_content(
                    str(row["id"]), neo4j=neo4j, chroma=chroma,
                )
                if result.found:
                    artifacts_purged += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
                log_swallowed_error(
                    "app.services.kb_hygiene.remove_artifact", exc,
                )

        if residue_entities:
            try:
                with neo4j.session() as session:
                    session.run(
                        "UNWIND $ids AS cid "
                        "MATCH (e:Entity {canonical_id: cid}) "
                        "DETACH DELETE e",
                        ids=[str(r["canonical_id"]) for r in residue_entities],
                    )
                entities_purged = len(residue_entities)
            except Exception as exc:  # noqa: BLE001 — entity purge failure is reported, not raised
                log_swallowed_error(
                    "app.services.kb_hygiene.remove_entities", exc,
                )

    summary = {
        "artifacts_found": len(residue_artifacts),
        "artifacts_purged": artifacts_purged,
        "entities_found": len(residue_entities),
        "entities_purged": entities_purged,
        "skipped_in_grace": skipped_in_grace,
        "samples": samples,
    }
    logger.info("kb_hygiene.sweep_test_residue apply=%s %s", apply, summary)
    return summary
