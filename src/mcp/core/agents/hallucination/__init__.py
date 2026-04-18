# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination Detection Agent — cross-references LLM responses against the KB.

Verification is evidence-based (embedding similarity + numeric alignment),
not LLM opinion. This avoids the trap of a verifier hallucinating agreement
with the main model. All signals are grounded in document evidence.

This package was decomposed from a single module into submodules for
maintainability.  All public symbols are re-exported here for backward
compatibility — callers can continue to use:
    ``from core.agents.hallucination import check_hallucinations``
"""

# Third-party imports needed by tests (patch targets reference these via
# the package namespace, e.g. ``agents.hallucination.httpx.AsyncClient``).
import httpx  # noqa: F401

# ---------------------------------------------------------------------------
# Re-exports from extraction.py
# ---------------------------------------------------------------------------
from core.agents.hallucination.extraction import (  # noqa: F401
    _detect_evasion,
    _extract_citation_claims,
    _extract_claims_heuristic,
    _extract_claims_llm,
    extract_claims,
)
from core.agents.hallucination.patterns import (  # noqa: F401
    _get_claim_verify_semaphore,
    _get_ext_verify_semaphore,
    _has_staleness_indicators,
    _is_complex_claim,
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
    _model_family,
    _pick_verification_model,
)

# ---------------------------------------------------------------------------
# Re-exports from persistence.py
# ---------------------------------------------------------------------------
from core.agents.hallucination.persistence import (  # noqa: F401
    REDIS_HALLUCINATION_PREFIX,
    REDIS_HALLUCINATION_TTL,
    get_hallucination_report,
)

# ---------------------------------------------------------------------------
# Re-exports from streaming.py
# ---------------------------------------------------------------------------
from core.agents.hallucination.streaming import (  # noqa: F401
    check_hallucinations,
    verify_response_streaming,
)

# ---------------------------------------------------------------------------
# Re-exports from verification.py
# ---------------------------------------------------------------------------
# System prompts (accessed by tests via inline import)
from core.agents.hallucination.verification import (  # noqa: F401
    _SYSTEM_CITATION_VERIFICATION,
    _SYSTEM_CONSISTENCY_CHECK,
    _SYSTEM_CURRENT_EVENT_VERIFICATION,
    _SYSTEM_EVASION_VERIFICATION,
    _SYSTEM_IGNORANCE_VERIFICATION,
    _SYSTEM_RECENCY_VERIFICATION,
    _build_verification_details,
    _check_history_consistency,
    _check_numeric_alignment,
    _compute_adjusted_confidence,
    _interpret_recency_verdict,
    _invert_evasion_verdict,
    _invert_ignorance_verdict,
    _kb_source_fields,
    _llm_call_with_retry,
    _parse_verification_verdict,
    _query_memories,
    _verify_claim_externally,
    verify_claim,
)

# Explicit public API. Private helpers above (prefixed `_`) are only re-exported
# for existing test patch targets and are NOT part of the public contract —
# tests import them directly from their concrete submodules in new code. When
# `from core.agents.hallucination import *` runs (agents/hallucination/__init__.py
# bridge), only these names are pulled; the bridge re-imports the private
# helpers explicitly for the same test-compatibility reason. Keeping __all__
# narrow lets import-linter + pyright enforce the layer boundary.
__all__ = [
    # Public functions
    "extract_claims",
    "check_hallucinations",
    "verify_response_streaming",
    "verify_claim",
    "get_hallucination_report",
    # Public constants
    "REDIS_HALLUCINATION_PREFIX",
    "REDIS_HALLUCINATION_TTL",
]
