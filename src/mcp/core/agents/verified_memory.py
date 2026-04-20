# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verified-fact-to-memory promotion — closes the verification → memory loop.

When the hallucination detection pipeline verifies a claim with high confidence
(NLI entailment ≥ threshold, verdict = "supported"), this module promotes it
to an empirical :Memory node with full provenance (VERIFIED_BY relationship
to the :VerificationReport, RELATES_TO the source :Artifact nodes).

Empirical memories have no decay curve — they persist permanently and receive
the highest authority boost during retrieval.

Fire-and-forget from streaming.py — never blocks the verification response.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import sentry_sdk

import config

logger = logging.getLogger("ai-companion.verified_memory")

# Per-claim async locks guarding the dedup-then-write critical section.
# Two concurrent verifications of the same claim text must serialize their
# detect_memory_conflict → create_memory path, otherwise both can pass the
# duplicate check before either commits the :Memory node and create duplicates.
# Keyed by normalized claim hash so different claims promote in parallel.
_PROMOTE_LOCKS: dict[str, asyncio.Lock] = {}


def _claim_lock(claim_text: str) -> asyncio.Lock:
    normalized = " ".join(claim_text.strip().lower().split())
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # dict.setdefault is atomic under the GIL — safe across coroutines
    return _PROMOTE_LOCKS.setdefault(key, asyncio.Lock())


async def promote_verified_facts(
    verification_report: dict[str, Any],
    chroma_client: Any,
    neo4j_driver: Any,
    redis_client: Any = None,
    *,
    min_confidence: float | None = None,
    min_nli_entailment: float | None = None,
    create_memory_fn: Any = None,
) -> dict[str, int]:
    """Promote high-confidence verified claims to empirical :Memory nodes.

    Only claims meeting ALL criteria are promoted:
    1. verdict == "supported"
    2. confidence >= min_confidence (default: 0.8)
    3. claim type is not ignorance/evasion (these are meta-claims, not facts)
    4. NLI entailment against KB source >= min_nli_entailment (default: 0.7)

    Creates :Memory nodes with memory_type="empirical", source="verification",
    and links them to the VerificationReport via VERIFIED_BY.

    Returns:
        {"promoted": int, "skipped_low_confidence": int,
         "skipped_duplicate": int, "skipped_type": int, "errors": int}
    """
    if min_confidence is None:
        min_confidence = getattr(config, "VERIFIED_MEMORY_MIN_CONFIDENCE", 0.8)
    if min_nli_entailment is None:
        min_nli_entailment = getattr(config, "VERIFIED_MEMORY_MIN_NLI", 0.7)

    claims = verification_report.get("claims", [])
    report_id = verification_report.get("conversation_id", "")

    counts = {
        "promoted": 0,
        "skipped_low_confidence": 0,
        "skipped_duplicate": 0,
        "skipped_type": 0,
        "errors": 0,
    }

    _SKIP_TYPES = {"ignorance", "evasion", "citation"}

    for claim_data in claims:
        try:
            verdict = claim_data.get("status", claim_data.get("verdict", ""))
            # Production claims use "similarity", verification reports use "confidence"
            confidence = float(claim_data.get("similarity", claim_data.get("confidence", 0.0)))
            claim_type = claim_data.get("type", "factual")
            claim_text = claim_data.get("claim", "")
            # NLI entailment from kb_nli path; cross-model verified claims use similarity as proxy
            nli_entailment = float(claim_data.get("nli_entailment", confidence))

            # Filter 1: Only "supported" / "verified" verdicts
            if verdict not in ("supported", "verified"):
                counts["skipped_low_confidence"] += 1
                continue

            # Filter 2: Must meet confidence bar
            if confidence < min_confidence:
                counts["skipped_low_confidence"] += 1
                continue

            # Filter 3: Skip meta-claim types and citation artifacts
            if claim_type in _SKIP_TYPES:
                counts["skipped_type"] += 1
                continue

            # Filter 4: NLI entailment bar (cross-model verified at 1.0 passes automatically)
            if nli_entailment < min_nli_entailment:
                counts["skipped_low_confidence"] += 1
                continue

            # Filter 5: Non-empty claim text
            if not claim_text or len(claim_text.strip()) < 10:
                counts["skipped_type"] += 1
                continue

            # Serialize dedup + write so two concurrent promotions of the
            # same claim cannot both pass detect_memory_conflict and create
            # duplicate :Memory nodes. Keyed per-claim so unrelated claims
            # still promote in parallel.
            async with _claim_lock(claim_text):
                # Dedup: check if equivalent memory already exists
                from core.agents.memory import detect_memory_conflict

                conflicts = await detect_memory_conflict(
                    claim_text,
                    chroma_client,
                    neo4j_driver,
                    similarity_threshold=0.90,  # Higher bar: near-duplicate
                )
                if conflicts:
                    counts["skipped_duplicate"] += 1
                    logger.debug(
                        "Verified fact already exists as memory (sim=%.3f): %s",
                        conflicts[0].get("similarity", 0),
                        claim_text[:60],
                    )
                    continue

                # Create the Memory node via injected callable (keeps core/ free of app/ imports)
                if create_memory_fn is None:
                    logger.warning("promote_verified_facts: no create_memory_fn provided, skipping")
                    counts["errors"] += 1
                    continue

                source_artifacts = claim_data.get("sources", [])
                primary_artifact_id = (
                    source_artifacts[0].get("artifact_id", "")
                    if source_artifacts
                    else ""
                )

                memory_id = create_memory_fn(neo4j_driver, {
                    "text": claim_text,
                    "memory_type": "empirical",
                    "source": "verification",
                    "confidence": confidence,
                    "base_score": confidence,
                    "artifact_id": primary_artifact_id,
                })

                # Link to VerificationReport via VERIFIED_BY relationship.
                #
                # Sprint C change: MATCH-only, no MERGE. Pre-Sprint C this
                # block did ``MERGE (r:VerificationReport {conversation_id:
                # $rid})`` which created stub nodes with nothing but a
                # conversation_id — the exact orphan pattern m0002
                # cleans up and I1 preservation gates detect. With auto-
                # persist on /agent/hallucination (Sprint C), the real
                # report is written AFTER this path runs; if we can't
                # find one yet, we skip the link (memory still carries
                # its own provenance via RELATES_TO). The link is a
                # cross-reference convenience, not required for any
                # retrieval correctness.
                if report_id and neo4j_driver:
                    try:
                        with neo4j_driver.session() as session:
                            linked = session.run(
                                "MATCH (m:Memory {id: $mid}), "
                                "      (r:VerificationReport {conversation_id: $rid}) "
                                "MERGE (m)-[:VERIFIED_BY]->(r) "
                                "RETURN count(*) AS n",
                                mid=memory_id,
                                rid=report_id,
                            ).single()
                            if not linked or linked["n"] == 0:
                                # Report not persisted yet — expected when
                                # auto-persist runs after this path.
                                logger.debug(
                                    "verified_memory.verified_by_skipped_no_report",
                                    extra={"memory_id": memory_id, "report_id": report_id},
                                )
                    except Exception:
                        logger.exception(
                            "verified_memory.verified_by_link_failed",
                            extra={"memory_id": memory_id, "report_id": report_id},
                        )
                        sentry_sdk.capture_exception()

                # Ingest the claim text into ChromaDB conversations collection
                # so it appears in future memory recall queries
                try:
                    from datetime import datetime, timezone

                    coll_name = config.collection_name("conversations")
                    collection = chroma_client.get_or_create_collection(name=coll_name)
                    now_iso = datetime.now(timezone.utc).isoformat()
                    collection.add(
                        ids=[f"verified_memory_{memory_id}"],
                        documents=[claim_text],
                        metadatas=[{
                            "artifact_id": memory_id,
                            "memory_type": "empirical",
                            "memory_source_type": "verification",
                            "domain": "conversations",
                            "filename": f"verified_fact_{memory_id[:8]}",
                            "ingested_at": now_iso,
                            "decay_anchor": now_iso,
                        }],
                    )
                except Exception:
                    logger.exception(
                        "verified_memory.chroma_ingest_failed",
                        extra={"memory_id": memory_id},
                    )
                    sentry_sdk.capture_exception()

                counts["promoted"] += 1
                logger.info(
                    "Promoted verified fact to memory (id=%s, conf=%.2f): %s",
                    memory_id, confidence, claim_text[:80],
                )

        except Exception:
            counts["errors"] += 1
            logger.debug("Verified memory promotion failed for claim", exc_info=True)

    if counts["promoted"]:
        logger.info(
            "Verified memory promotion complete: %d promoted, %d skipped (low_conf=%d, dup=%d, type=%d), %d errors",
            counts["promoted"],
            counts["skipped_low_confidence"] + counts["skipped_duplicate"] + counts["skipped_type"],
            counts["skipped_low_confidence"],
            counts["skipped_duplicate"],
            counts["skipped_type"],
            counts["errors"],
        )

    return counts
