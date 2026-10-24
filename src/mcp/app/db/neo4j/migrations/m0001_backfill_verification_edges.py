"""m0001: Bring every stored verification report's source edges and
provenance properties in line with what the writer stores today.

Idempotent: a report already in line is read and left alone, so a second run
changes nothing. Runs at every boot after m0008 (``verification_reports.
migrate_reports``) and from ``scripts.run_migrations``.

Per report (addressed by node, so a report written before m0008 keyed it is
covered too):

* an EXTRACTED_FROM and a VERIFIED edge to every cited ``:Artifact`` or
  ``:Memory`` that exists and is not forgotten, none to a forgotten one, and
  ``edge_status`` saying which source landed and why each other did not
  (invariant S11-3; ``artifacts.link_cited_sources``);
* ``source_urls`` and ``verification_methods`` from its claims.

Claim-shape normalization is delegated to
``core.agents.hallucination.models.ClaimVerification.from_legacy_dict``,
the adapter the production writer uses.
"""
from __future__ import annotations

import json
import logging

from app.db.neo4j.artifacts import forgotten_source_ids, link_cited_sources
from core.agents.hallucination.models import ClaimVerification

logger = logging.getLogger("ai-companion.migrations.m0001")

REPORTS_CYPHER = (
    "MATCH (r:VerificationReport) RETURN elementId(r) AS eid, r.claims AS claims, "
    "r.source_urls AS source_urls, r.verification_methods AS methods"
)

ANNOTATE_CYPHER = (
    "MATCH (r:VerificationReport) WHERE elementId(r) = $rid "
    "SET r.source_urls = $urls, r.verification_methods = $methods"
)


def run(driver) -> dict[str, int]:
    stats = {
        "reports_scanned": 0, "edges_added": 0, "edges_removed": 0,
        "statuses_written": 0, "reports_annotated": 0,
    }
    forgotten = forgotten_source_ids()
    with driver.session() as session:
        rows = list(session.run(REPORTS_CYPHER))
        stats["reports_scanned"] = len(rows)
        for row in rows:
            try:
                raw_claims = json.loads(row["claims"] or "[]")
            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.db.neo4j.migrations.m0001_backfill_verification_edges', exc)
                continue
            aids: set[str] = set()
            urls: set[str] = set()
            methods: set[str] = set()
            for raw in raw_claims:
                try:
                    c = ClaimVerification.from_legacy_dict(raw)
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.db.neo4j.migrations.m0001_backfill_verification_edges', exc)
                    continue
                if c.verification_method:
                    methods.add(c.verification_method)
                urls.update(u for u in c.source_urls if isinstance(u, str) and u)
                aids.update(c.artifact_ids())
            try:
                changes = link_cited_sources(session, row["eid"], aids, forgotten)
            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.db.neo4j.migrations.m0001_backfill_verification_edges', exc)
                continue
            for key, n in changes.items():
                stats[key] += n
            # Replace, never append: the claims JSON is the source of truth.
            if (urls or methods) and (
                sorted(urls) != list(row["source_urls"] or []) or sorted(methods) != list(row["methods"] or [])
            ):
                session.run(ANNOTATE_CYPHER, rid=row["eid"], urls=sorted(urls), methods=sorted(methods))
                stats["reports_annotated"] += 1
    logger.info("migration m0001 done: %s", stats)
    return stats
