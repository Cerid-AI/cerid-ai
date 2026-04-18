"""m0001: Backfill [:EXTRACTED_FROM] edges + provenance props for
VerificationReport nodes written prior to the Task-2 fix.

Idempotent. Safe to re-run.
"""
from __future__ import annotations

import json
import logging

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
                claims = json.loads(row["claims"] or "[]")
            except Exception:
                continue
            aids: set[str] = set()
            urls: set[str] = set()
            methods: set[str] = set()
            for c in claims:
                if c.get("verification_method"):
                    methods.add(c["verification_method"])
                # Claim-level flat URL list (actual shape emitted by pipeline)
                for u in c.get("source_urls", []) or []:
                    if isinstance(u, str) and u:
                        urls.add(u)
                # Structured sources (future shape + KB matches)
                for s in c.get("sources", []) or []:
                    if s.get("artifact_id"):
                        aids.add(s["artifact_id"])
                    url = s.get("url") or s.get("source_url")
                    if url:
                        urls.add(url)
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
