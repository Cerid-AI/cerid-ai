# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Generation-time claim verification for briefs (Task 2.1b).

Runs the existing claim-extraction + KB-verification pipeline
(:mod:`core.agents.hallucination`) against a freshly generated brief's
sections and maps each verdict to a conservative trust ``band`` so the
Briefs read API (Task 2.1a) can render real ``verified|partial|unverified``
values instead of the always-empty ``claim_ids`` list ``generate_daily`` /
``generate_weekly`` produce today.

This module owns exactly one responsibility: turn brief sections into a
list of banded claim dicts. It does NOT persist anything — callers pass
the result to :func:`app.db.neo4j.briefs.save_verified_claims`. It also
does NOT re-implement claim extraction or NLI verification; both are
reused verbatim from :mod:`core.agents.hallucination`.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# Wall-clock budget (seconds) for verifying every claim in one brief, threaded
# to :func:`verify_claims` as a monotonic deadline shared across the batch.
# Briefs are a background/offline path with no user waiting on an SSE stream, so
# the bound is generous relative to the per-turn chat budget — but it MUST exist
# so a stalled cross-model/web verification can't hang brief generation forever.
BRIEF_VERIFY_DEADLINE_S = 60.0

# Conservative status -> band mapping. Anything not explicitly "verified"
# or "uncertain" collapses to "unverified" so the UI never over-claims
# confidence the pipeline can't back up.
_BAND_BY_STATUS: dict[str, str] = {
    "verified": "verified",
    "uncertain": "partial",
    "unverified": "unverified",
    "error": "unverified",
}


def status_to_band(status: str) -> str:
    """Map a ``verify_claims()`` verdict status to a UI trust band."""
    return _BAND_BY_STATUS.get(status, "unverified")


async def verify_brief_claims(
    sections: dict[str, str],
    *,
    chroma_client: Any,
    neo4j_driver: Any = None,
    redis_client: Any = None,
) -> list[dict[str, Any]]:
    """Extract and verify claims from a brief's generated sections.

    Returns one dict per surfaced claim: ``{claim_id, text, band,
    source_ids}``. Returns ``[]`` when the sections carry no verifiable
    claims — empty body, or ``extract_claims`` reports an
    ignorance/evasion/non-factual response — callers treat that as
    "nothing to persist", never as an error. A per-claim verification
    failure does not raise here either: ``verify_claims`` already
    isolates it to an ``error`` verdict, which maps to ``"unverified"``.
    """
    from core.agents.hallucination import extract_claims, verify_claims
    from core.agents.hallucination.patterns import _has_staleness_indicators

    body = "\n\n".join(
        text.strip() for text in sections.values() if text and text.strip()
    )
    if not body:
        return []

    claims, method = await extract_claims(body)
    if not claims or method in ("ignorance", "evasion", "none"):
        return []

    # Propagate the two integrity signals the facade forwards to verify_claim:
    # a stale-cutoff admission anywhere in the brief routes its factual claims to
    # a live web check instead of a KB echo, and the shared deadline bounds total
    # external work so brief generation can't hang on a slow verifier.
    verdicts = await verify_claims(
        claims,
        chroma_client,
        neo4j_driver=neo4j_driver,
        redis_client=redis_client,
        stale_context=_has_staleness_indicators(body),
        deadline=time.monotonic() + BRIEF_VERIFY_DEADLINE_S,
    )

    results: list[dict[str, Any]] = []
    for claim_text, verdict in zip(claims, verdicts):
        status = verdict.get("status", "error")
        source_artifact_id = verdict.get("source_artifact_id")
        results.append(
            {
                "claim_id": str(uuid.uuid4()),
                "text": claim_text,
                "band": status_to_band(status),
                "source_ids": [source_artifact_id] if source_artifact_id else [],
            }
        )
    return results
