"""m0002: Delete pre-v0.84.x :VerificationReport nodes with no provenance.

Idempotent. Safe to re-run.

After the v0.84.1 writer fix (see ``save_verification_report``
recognizing flat ``source_artifact_id``), newly saved reports get
proper [:EXTRACTED_FROM] / [:VERIFIED] edges and/or populated
``source_urls`` / ``verification_methods`` arrays. Any
:VerificationReport that has NONE of these channels is a legacy
artifact from one of three sources:

    1. A smoke-test fixture ({conversation_id: "smoke-paris"}).
    2. A pre-v0.84.0 report whose referenced artifact was since
       deleted (``m0001`` couldn't backfill it because the target
       node was gone).
    3. A report saved by the broken v0.84.0 writer before the
       flat-shape fix landed.

None of these carry actionable provenance. DETACH DELETE clears
them, bringing ``/health.invariants.verification_report_orphans``
to 0. After this migration runs, the startup invariant becomes a
true regression signal — any orphan thereafter is a writer bug.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0002")

# DETACH DELETE is required — the orphan may carry incidental
# [:VERIFIED_BY] edges from stray Memory nodes (the MERGE pattern in
# ``core/agents/verified_memory.py`` will recreate them against the
# real report once one is saved for the same conversation_id).
_ORPHAN_CLEANUP_CYPHER = """
MATCH (r:VerificationReport)
WHERE NOT (r)-[:VERIFIED]->() AND NOT (r)-[:EXTRACTED_FROM]->()
  AND (r.source_urls IS NULL OR size(r.source_urls) = 0)
  AND (r.verification_methods IS NULL OR size(r.verification_methods) = 0)
WITH r, r.conversation_id AS cid
DETACH DELETE r
RETURN count(*) AS deleted, collect(DISTINCT cid)[0..10] AS sample_cids
"""


def run(driver) -> dict:
    stats: dict = {"orphans_deleted": 0, "sample_conversation_ids": []}
    with driver.session() as session:
        row = session.run(_ORPHAN_CLEANUP_CYPHER).single()
        if row is not None:
            stats["orphans_deleted"] = int(row["deleted"])
            stats["sample_conversation_ids"] = list(row["sample_cids"] or [])
    logger.info("migration m0002 done: %s", stats)
    return stats
