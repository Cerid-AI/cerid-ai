"""m0001: Backfill [:EXTRACTED_FROM] edges + provenance props for
VerificationReport nodes written prior to the Task-2 fix.

Idempotent. Safe to re-run.

Claim-shape normalization is delegated to
``core.agents.hallucination.models.ClaimVerification.from_legacy_dict``
— same adapter the production writer uses. Before Sprint B of the
consolidation program, this migration carried a duplicate copy of
the "flat vs nested vs legacy" shape-detection logic and got out
of sync with the writer (P1.4). One adapter, one source of truth.
"""
from __future__ import annotations

import json
import logging

from core.agents.hallucination.models import ClaimVerification

logger = logging.getLogger("ai-companion.migrations.m0001")


def run(driver) -> dict:
    stats = {"reports_scanned": 0, "edges_added": 0, "reports_annotated": 0}
    with driver.session() as session:
        rows = list(session.run(
            "MATCH (r:VerificationReport) RETURN r.id AS id, r.conversation_id AS cid, r.claims AS claims"
        ))
        stats["reports_scanned"] = len(rows)
        for row in rows:
            try:
                raw_claims = json.loads(row["claims"] or "[]")
            except Exception:
                continue
            aids: set[str] = set()
            urls: set[str] = set()
            methods: set[str] = set()
            for raw in raw_claims:
                try:
                    c = ClaimVerification.from_legacy_dict(raw)
                except Exception:
                    continue
                if c.verification_method:
                    methods.add(c.verification_method)
                urls.update(u for u in c.source_urls if isinstance(u, str) and u)
                aids.update(c.artifact_ids())
            if aids:
                r = session.run(
                    """
                    UNWIND $aids AS aid
                    MATCH (report:VerificationReport {conversation_id: $cid})
                    MATCH (a:Artifact {id: aid})
                    MERGE (report)-[:EXTRACTED_FROM]->(a)
                    MERGE (report)-[:VERIFIED]->(a)
                    RETURN count(*) AS n
                    """,
                    cid=row["cid"],
                    aids=list(aids),
                )
                stats["edges_added"] += (r.single() or {}).get("n", 0)
            if urls or methods:
                # Idempotent: replace (not append) — claims JSON is the source of truth.
                session.run(
                    """
                    MATCH (r:VerificationReport {conversation_id: $cid})
                    SET r.source_urls = $urls,
                        r.verification_methods = $methods
                    """,
                    cid=row["cid"],
                    urls=sorted(urls),
                    methods=sorted(methods),
                )
                stats["reports_annotated"] += 1
    logger.info("migration m0001 done: %s", stats)
    return stats
