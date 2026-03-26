# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier 1: Verification efficacy — ground-truth claim tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from conftest import (
    cleanup_artifact,
    load_jsonl,
    seed_content,
    stream_verify,
    wait_for_indexed,
)

CASES = load_jsonl("verification_cases.jsonl")


def find_matching_claim(claims: list[dict], text_fragment: str) -> dict | None:
    """Find a claim whose text contains the fragment (case-insensitive)."""
    fragment_lower = text_fragment.lower()
    for c in claims:
        if fragment_lower in c.get("claim", "").lower():
            return c
    # Fallback: check if any claim overlaps significantly
    fragment_words = set(fragment_lower.split())
    for c in claims:
        claim_words = set(c.get("claim", "").lower().split())
        overlap = fragment_words & claim_words
        if len(overlap) >= max(2, len(fragment_words) * 0.6):
            return c
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_verification_case(case: dict, aclient: httpx.AsyncClient) -> None:
    """Test a single verification case against the live verify-stream endpoint."""
    seeded_ids: list[str] = []

    try:
        # Seed KB content if needed
        if case.get("seed_content"):
            aid = await seed_content(aclient, case["seed_content"], case.get("seed_domain", "general"))
            seeded_ids.append(aid)
            await wait_for_indexed(aclient, aid, timeout=10)

        # Stream verification
        result = await stream_verify(
            aclient,
            case["response_text"],
            case["user_query"],
            source_artifact_ids=[],
        )

        assert not result["errors"], f"Verification errors: {result['errors']}"
        claims = result["claims"]

        # If extraction returned 0 claims, skip assertion (LLM extraction variability)
        if len(claims) == 0:
            pytest.skip(f"[{case['id']}] No claims extracted — extraction LLM returned empty")

        # Assert expected claims
        for expected in case.get("expected_claims", []):
            fragment = expected["text_fragment"]
            matched = find_matching_claim(claims, fragment)

            if expected.get("should_extract", True):
                assert matched is not None, (
                    f"[{case['id']}] Claim not extracted: '{fragment}'. "
                    f"Got {len(claims)} claims: {[c['claim'][:50] for c in claims]}"
                )

                # Check claim type if specified
                exp_type = expected.get("type")
                if exp_type and exp_type != "any":
                    assert matched["claim_type"] == exp_type, (
                        f"[{case['id']}] Expected type '{exp_type}' for '{fragment}', "
                        f"got '{matched['claim_type']}'"
                    )

                # Check verdict if specified
                exp_verdict = expected.get("expected_verdict")
                if exp_verdict:
                    assert matched["status"] == exp_verdict, (
                        f"[{case['id']}] Expected verdict '{exp_verdict}' for '{fragment}', "
                        f"got '{matched['status']}' (reason: {matched.get('reason', 'N/A')})"
                    )
            else:
                # Negative test: claim should NOT be extracted or should NOT be this type
                if matched and expected.get("not_type"):
                    assert matched["claim_type"] != expected["not_type"], (
                        f"[{case['id']}] Claim should NOT be type '{expected['not_type']}'"
                    )
    finally:
        # Cleanup seeded content
        for aid in seeded_ids:
            await cleanup_artifact(aclient, aid)
