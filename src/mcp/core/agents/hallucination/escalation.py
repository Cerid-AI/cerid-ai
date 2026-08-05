# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Trust-or-escalate policy for claim verification (Phase 3.2).

``verify_claim`` decides, at several inline forks, whether to trust the local KB
grounding or fall back to an external verifier — and, when it escalates, whether
a cross-model verdict suffices or the claim needs live web search. That decision
was implicit: the temporal→web predicate was duplicated three times and the
"is this KB match semantically aligned" floor was a bare ``0.15`` literal repeated
across branches.

This module makes the decision an explicit, unit-testable seam that sits between
the grounding stage (``_score_kb_grounding``'s similarity / nli output) and the
escalation branches. The three tiers name the plan's cost shape:

* ``TRUST_KB``   — the local grounding is confident; no external call.
* ``CROSS_MODEL``— low-confidence grounding; a cross-model verifier may resolve it.
* ``WEB``        — time-sensitive or contested; even strong KB entailment can be
  stale, so force a web-search verifier.

The DEFAULT policy reproduces ``verify_claim``'s pre-3.2 decisions exactly — it is
the seam, not a behavior change. Its inputs (grounding signals, temporality,
claim type) are the same signals the inline branches already consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import config
from core.agents.hallucination.patterns import (
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
)

# Minimum NLI entailment for a high-similarity KB match to count as *about* the
# claim's assertion (vs. sharing keywords on an orthogonal topic). Named
# extraction of the bare 0.15 floor that was inlined across verify_claim's
# semantic-alignment and contradiction-authority gates.
KB_SEMANTIC_ALIGNMENT_FLOOR = 0.15

# Prefix stamped on synthesized evasion claims by extraction._detect_evasion.
_EVASION_PREFIX = "[EVASION]"


class EscalationTier(str, Enum):
    """Where a claim's verification should be resolved."""

    TRUST_KB = "trust_kb"
    CROSS_MODEL = "cross_model"
    WEB = "web"


@dataclass(frozen=True)
class GroundingSignals:
    """The grounding-stage signals the escalation decision consumes.

    ``raw_similarity`` is the pre-calibration top-result relevance the escalation
    branches gate on; ``similarity`` is the calibrated confidence carried into
    the verdict. Both are kept because the contradiction-authority gate reads the
    raw value while the trust decision reads the calibrated one.
    """

    similarity: float
    raw_similarity: float
    entailment: float
    contradiction: float


class EscalationPolicy:
    """Pure trust-or-escalate decision over grounding signals + claim character.

    Constructed per ``verify_claim`` call with the thresholds that call resolved
    (``verified_threshold`` may be a caller override of
    ``HALLUCINATION_THRESHOLD``), so the policy tracks the same numbers the
    branches used to read from ``config`` inline.
    """

    def __init__(
        self,
        *,
        verified_threshold: float,
        entailment_threshold: float,
        semantic_floor: float = KB_SEMANTIC_ALIGNMENT_FLOOR,
    ) -> None:
        self.verified_threshold = verified_threshold
        self.entailment_threshold = entailment_threshold
        self.semantic_floor = semantic_floor

    def is_temporal(self, claim: str, *, stale_context: bool) -> bool:
        """True when the claim's currency matters — the WEB-vs-CROSS_MODEL signal.

        Reproduces verify_claim's inline temporal predicate (recency- or
        current-event-classified, or extracted from a stale-cutoff response).
        A time-sensitive claim cannot be trusted on KB entailment alone: the KB
        text can match while the underlying value has moved on.
        """
        return (
            _is_recency_claim(claim)
            or _is_current_event_claim(claim)
            or stale_context
        )

    def semantic_alignment_ok(self, signals: GroundingSignals) -> bool:
        """True when NLI entailment clears the floor — the KB match is *about*
        the claim, not merely keyword-overlapping an orthogonal topic."""
        return signals.entailment >= self.semantic_floor

    def kb_contradiction_authoritative(self, signals: GroundingSignals) -> bool:
        """True when an NLI contradiction is trustworthy as a terminal verdict.

        Two signals must agree: the KB source is topically strong
        (``raw_similarity >= verified_threshold``) AND semantically about the
        claim (entailment clears the floor). High contradiction with near-zero
        entailment is the "different topic, same keywords" signature — not a real
        contradiction — and escalates instead of hard-failing.
        """
        return (
            signals.raw_similarity >= self.verified_threshold
            and self.semantic_alignment_ok(signals)
        )

    def classify(
        self, signals: GroundingSignals, *, is_temporal: bool
    ) -> EscalationTier:
        """Escalation tier for a positively-grounded claim.

        The declarative statement of the trust-or-escalate shape: time-sensitive
        claims go to WEB even on strong entailment; a high-similarity match whose
        entailment is below the alignment floor is untrustworthy on keyword
        overlap alone (CROSS_MODEL); otherwise the local grounding is trusted.
        """
        if is_temporal:
            return EscalationTier.WEB
        if signals.entailment >= self.entailment_threshold:
            return EscalationTier.TRUST_KB
        if not self.semantic_alignment_ok(signals):
            return EscalationTier.CROSS_MODEL
        return EscalationTier.TRUST_KB

    def type_route(self, claim: str) -> EscalationTier | None:
        """Pre-grounding route for claim types the KB path cannot grade (Phase 3.5).

        Evasion and ignorance claims assert something about the *model's stance*
        ("I don't have information about X"; a hedge), not a fact the KB can
        confirm. Grading their literal text against KB similarity rubber-stamps a
        hedge whose surface content happens to be grounded. They must go straight
        to the type-aware external verifier (which inverts on whether the
        underlying facts actually exist). Returns ``WEB`` for those types, else
        ``None`` (grade normally).
        """
        if claim.startswith(_EVASION_PREFIX) or _is_ignorance_admission(claim):
            return EscalationTier.WEB
        return None


def get_escalation_policy(
    *,
    verified_threshold: float,
    entailment_threshold: float | None = None,
) -> EscalationPolicy:
    """Build the default escalation policy for a verify_claim invocation."""
    return EscalationPolicy(
        verified_threshold=verified_threshold,
        entailment_threshold=(
            entailment_threshold
            if entailment_threshold is not None
            else config.NLI_ENTAILMENT_THRESHOLD
        ),
    )
