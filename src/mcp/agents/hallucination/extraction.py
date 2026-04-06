# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see core/agents/hallucination/extraction.py for implementation.
from core.agents.hallucination.extraction import *  # noqa: F401,F403
from core.agents.hallucination.extraction import (
    _detect_evasion,  # noqa: F401
    _extract_claims_heuristic,  # noqa: F401
    _reclassify_recency,  # noqa: F401
)
