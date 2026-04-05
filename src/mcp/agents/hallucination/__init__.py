# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see core/agents/hallucination/ for implementation.
from core.agents.hallucination import *  # noqa: F401,F403
from core.agents.hallucination import (  # noqa: F401
    _check_numeric_alignment,
    _compute_adjusted_confidence,
    _detect_evasion,
    _extract_claims_heuristic,
)
