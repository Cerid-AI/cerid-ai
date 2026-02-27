# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination Detection Agent — cross-references LLM responses against the KB."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

import config
from utils.llm_parsing import parse_llm_json
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.hallucination")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 0.75
UNVERIFIED_THRESHOLD = 0.4
MAX_CLAIMS_PER_RESPONSE = 10
MIN_RESPONSE_LENGTH = 100  # skip very short responses

REDIS_HALLUCINATION_PREFIX = "hall:"
REDIS_HALLUCINATION_TTL = 86400 * 7  # 7 days


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

async def extract_claims(response_text: str) -> List[str]:
    """Extract factual claims from an LLM response using a lightweight model."""
    if len(response_text) < MIN_RESPONSE_LENGTH:
        return []

    prompt = (
        "Extract the key factual claims from the following text. "
        "Return ONLY a JSON array of strings, each being a single factual statement. "
        "Focus on verifiable facts, not opinions or greetings. "
        f"Return at most {MAX_CLAIMS_PER_RESPONSE} claims.\n\n"
        f"Text:\n{response_text[:3000]}\n\n"
        "JSON array:"
    )

    try:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT) as client:
            resp = await client.post(
                f"{config.BIFROST_URL}/chat/completions",
                json={
                    "model": config.LLM_INTERNAL_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 800,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            claims = parse_llm_json(content)
            if isinstance(claims, list):
                return [str(c) for c in claims[:MAX_CLAIMS_PER_RESPONSE]]
    except Exception as e:
        logger.warning(f"Claim extraction failed: {e}")

    return []


# ---------------------------------------------------------------------------
# Claim verification
# ---------------------------------------------------------------------------

async def verify_claim(
    claim: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: float = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    """Verify a single claim against the knowledge base."""
    from agents.query_agent import agent_query

    try:
        # Exclude 'conversations' domain to avoid self-verification:
        # feedback-ingested responses would match their own claims.
        verification_domains = [d for d in config.DOMAINS if d != "conversations"]
        result = await agent_query(
            query=claim,
            domains=verification_domains,
            top_k=3,
            use_reranking=False,  # speed over accuracy for verification
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
        )

        if not result.get("results"):
            return {
                "claim": claim,
                "status": "unverified",
                "similarity": 0.0,
                "reason": "No matching KB content found",
            }

        top_result = result["results"][0]
        similarity = top_result.get("relevance", 0.0)

        if similarity >= threshold:
            return {
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
            }
        elif similarity < UNVERIFIED_THRESHOLD:
            return {
                "claim": claim,
                "status": "unverified",
                "similarity": round(similarity, 3),
                "reason": "Low similarity to any KB content",
            }
        else:
            return {
                "claim": claim,
                "status": "uncertain",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "reason": "Partial match — review recommended",
            }

    except Exception as e:
        logger.warning(f"Claim verification failed for '{claim[:50]}...': {e}")
        return {
            "claim": claim,
            "status": "error",
            "similarity": 0.0,
            "reason": str(e),
        }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def check_hallucinations(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Extract claims, verify each against KB, and store results in Redis."""
    if threshold is None:
        threshold = float(getattr(config, "HALLUCINATION_THRESHOLD", DEFAULT_THRESHOLD))

    if len(response_text) < MIN_RESPONSE_LENGTH:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars < {MIN_RESPONSE_LENGTH})",
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    claims = await extract_claims(response_text)
    if not claims:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "skipped": True,
            "reason": "No factual claims extracted",
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }

    import asyncio

    results = await asyncio.gather(*[
        verify_claim(claim, chroma_client, neo4j_driver, redis_client, threshold)
        for claim in claims
    ])

    status_counts = {"verified": 0, "unverified": 0, "uncertain": 0, "error": 0}
    for r in results:
        status = r.get("status", "error")
        if status in status_counts:
            status_counts[status] += 1

    report = {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "skipped": False,
        "threshold": threshold,
        "claims": results,
        "summary": {
            "total": len(results),
            **status_counts,
        },
    }

    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        redis_client.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))
    except Exception as e:
        logger.warning(f"Failed to store hallucination report in Redis: {e}")

    return report


async def verify_response_streaming(
    response_text: str,
    conversation_id: str,
    chroma_client,
    neo4j_driver,
    redis_client,
    threshold: Optional[float] = None,
):
    """Streaming verification generator — yields claim results as they are verified."""
    if threshold is None:
        threshold = float(getattr(config, "HALLUCINATION_THRESHOLD", DEFAULT_THRESHOLD))

    if len(response_text) < MIN_RESPONSE_LENGTH:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": f"Response too short ({len(response_text)} chars)",
        }
        return

    claims = await extract_claims(response_text)
    if not claims:
        yield {
            "type": "summary",
            "overall_confidence": 0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "skipped": True,
            "reason": "No factual claims extracted",
        }
        return

    for i, claim in enumerate(claims):
        yield {"type": "claim_extracted", "claim": claim, "index": i}

    verified_count = 0
    unverified_count = 0
    uncertain_count = 0
    total_confidence = 0.0

    for i, claim in enumerate(claims):
        result = await verify_claim(
            claim, chroma_client, neo4j_driver, redis_client, threshold
        )
        status = result.get("status", "error")
        confidence = result.get("similarity", 0.0)

        if status == "verified":
            verified_count += 1
        elif status == "unverified":
            unverified_count += 1
        else:
            uncertain_count += 1

        total_confidence += confidence

        yield {
            "type": "claim_verified",
            "index": i,
            "status": status,
            "confidence": confidence,
            "source": result.get("source_filename", ""),
            "reason": result.get("reason", ""),
        }

    overall = (total_confidence / len(claims)) if claims else 0
    yield {
        "type": "summary",
        "overall_confidence": round(overall, 3),
        "verified": verified_count,
        "unverified": unverified_count,
        "uncertain": uncertain_count,
        "total": len(claims),
    }


def get_hallucination_report(
    redis_client,
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve a previously stored hallucination report."""
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        data = redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to retrieve hallucination report: {e}")
    return None