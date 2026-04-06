# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-export bridge — implementation lives in core.utils.cache."""

from core.utils.cache import *  # noqa: F401,F403
from core.utils.cache import (  # noqa: F401
    get_log,
    log_claim_feedback,
    log_conversation_metrics,
    log_event,
    log_verification_error,
    log_verification_metrics,
)
