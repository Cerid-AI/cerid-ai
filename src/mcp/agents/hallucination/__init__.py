# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see core/agents/hallucination/ for implementation.
from core.agents.hallucination import *  # noqa: F401,F403

# Private functions needed by tests (star import skips _ names)
from core.agents.hallucination import (  # noqa: F401
    _check_numeric_alignment,
    _compute_adjusted_confidence,
    _detect_evasion,
    _extract_claims_heuristic,
    _has_staleness_indicators,
    _interpret_recency_verdict,
    _invert_evasion_verdict,
    _invert_ignorance_verdict,
    _is_complex_claim,
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
    _parse_verification_verdict,
)
