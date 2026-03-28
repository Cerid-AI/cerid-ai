# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verdict parsing and interpretation — extracted from verification.py.

Dependencies: patterns.py.
Error types: VerificationError.
"""

from __future__ import annotations

import json
from typing import Any

from utils.llm_parsing import parse_llm_json

__all__ = [
    "_parse_verification_verdict",
    "_interpret_recency_verdict",
    "_invert_ignorance_verdict",
    "_invert_evasion_verdict",
]


# ---------------------------------------------------------------------------
# Verdict inversion helpers
# ---------------------------------------------------------------------------

def _invert_ignorance_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Invert a verification verdict for an ignorance-admitting claim.

    When a model says "I don't know about X" and the verifier confirms X
    exists (verdict = "verified"), the model's response was factually
    inadequate — it should be marked as *unverified* (refuted in the UI).

    Conversely, if the verifier says the underlying facts don't exist
    (verdict = "unverified"), the model was correct to say it doesn't have
    that information — mark as *verified*.

    Confidence is preserved: high verifier confidence in the existence of
    the facts means high confidence in the refutation.
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Verifier confirms the underlying facts exist → model was wrong
        # to say it doesn't have the information (response was inadequate).
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": (
                f"Response was factually inadequate — the information exists: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    if status == "unverified":
        # Verifier says the underlying facts don't exist → model was
        # correct that it has no information about this topic.
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "confidence": max(verdict.get("confidence", 0.5), 0.7),
            "reason": (
                f"Model correctly identified lack of information: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    # uncertain / error — keep as-is
    return verdict


def _invert_evasion_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Invert a verification verdict for an evasion claim.

    When a model evades answering and Grok finds the actual data
    (verdict = "verified"/supported), the model's evasion was unjustified
    → mark as "unverified" (refuted in the UI).

    If Grok confirms the data genuinely doesn't exist ("unverified"/refuted),
    the model's caution was justified → mark as "verified".
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Data exists — model's evasion was unjustified
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": (
                f"Model evaded answering — data is available: {clean_reason}"
            ).rstrip(": "),
        }

    if status == "unverified":
        # Data genuinely unavailable — evasion was justified
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "confidence": max(verdict.get("confidence", 0.5), 0.7),
            "reason": (
                f"Model's caution was justified — data is unavailable: "
                f"{clean_reason}"
            ).rstrip(": "),
        }

    # uncertain / error — keep as-is
    return verdict


def _interpret_recency_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Interpret a verification verdict for a recency/staleness claim.

    Unlike ignorance inversion, recency verdicts map directly:
    - "supported" → model's data is still current → "verified"
    - "refuted" → model's data is outdated → "unverified" with current data
    - "uncertain" → keep as-is
    """
    status = verdict["status"]
    reasoning = verdict.get("reason", "")

    if status == "verified":
        # Model's data confirmed as current
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification confirmed: ",
            "Cross-model verification confirmed",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "verified",
            "reason": f"Data confirmed current: {clean_reason}".rstrip(": "),
        }

    if status == "unverified":
        # Model's data is outdated — newer data available
        clean_reason = reasoning
        for prefix in (
            "Cross-model verification found factual errors: ",
            "Cross-model verification found factual errors",
        ):
            if clean_reason.startswith(prefix):
                clean_reason = clean_reason[len(prefix):]
                break
        return {
            **verdict,
            "status": "unverified",
            "reason": f"Outdated: {clean_reason}".rstrip(": "),
        }

    return verdict


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

def _parse_verification_verdict(raw: str) -> dict[str, Any]:
    """Parse structured JSON verdict from a direct verification model response.

    Expected format: {"verdict": "supported"|"refuted"|"insufficient_info",
                      "confidence": 0.0-1.0, "reasoning": "..."}

    Falls back to heuristic parsing if JSON is malformed.
    """
    if not raw or not raw.strip():
        return {
            "status": "uncertain",
            "confidence": 0.3,
            "reason": "Empty verification response",
        }

    # Try JSON parsing (handles markdown-wrapped ```json blocks too)
    try:
        parsed = parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError, KeyError):
        parsed = None
    if isinstance(parsed, dict) and "verdict" in parsed:
        verdict = str(parsed["verdict"]).lower().strip()
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(parsed.get("reasoning", ""))

        if verdict == "supported" and confidence >= 0.5:
            status = "verified"
        elif verdict == "refuted":
            status = "unverified"
            # Refuted claims get low confidence even if model says high
            confidence = min(confidence, 0.35)
        else:
            # insufficient_info, unrecognized, or low-confidence supported
            # → uncertain (neutral).  Unassessable claims get a truly neutral
            # score so they don't drag down the overall confidence average.
            status = "uncertain"
            confidence = 0.5

        reason_prefix = {
            "verified": "Cross-model verification confirmed",
            "unverified": "Cross-model verification found factual errors",
            "uncertain": "Claim not independently verifiable",
        }[status]

        reason = f"{reason_prefix}: {reasoning}" if reasoning else reason_prefix

        return {
            "status": status,
            "confidence": round(confidence, 3),
            "reason": reason,
        }

    # Fallback: model returned free text instead of JSON —
    # look for strong signal words as a last resort
    lower = raw.lower()
    if any(w in lower for w in ("incorrect", "false", "wrong", "inaccurate", "not true")):
        return {
            "status": "unverified",
            "confidence": 0.3,
            "reason": "Cross-model verification found inconsistencies (non-JSON response)",
        }
    if any(w in lower for w in ("correct", "accurate", "true", "confirmed", "yes,")):
        return {
            "status": "verified",
            "confidence": 0.65,
            "reason": "Cross-model verification confirmed (non-JSON response)",
        }

    return {
        "status": "uncertain",
        "confidence": 0.5,
        "reason": "Claim not independently verifiable (unparseable response)",
    }
