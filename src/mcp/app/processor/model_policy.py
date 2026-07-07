# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""App-layer wrapper around ``core.processor.mode.select_job_model``.

Reads the live ``PROCESSOR_MODE`` / cap settings, fetches the current
month's spend from Redis only when a hybrid job actually exceeds the API
token threshold, and delegates the actual routing decision to the pure
core function. Called by ``app.processor.worker`` for every LLM-backed job.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from config import settings
from core.processor.mode import ModelDecision, resolve_processor_mode, select_job_model
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.model_policy")

_CAP_FALLBACK_REASONS = frozenset({"hybrid_cap_fallback_local", "hybrid_cap_hold"})


async def resolve_job_model(
    redis_client: Any,
    *,
    default_local: str = "ollama/local",
    api_model: str,
    estimated_tokens: int,
) -> ModelDecision:
    """Resolve which model a job should run on under the current processor mode."""
    mode = resolve_processor_mode(settings.PROCESSOR_MODE)

    monthly_spend_usd = Decimal("0")
    if mode == "hybrid" and estimated_tokens > settings.PROCESSOR_API_THRESHOLD_TOKENS:
        try:
            from app.processor.metrics import processor_cost_usd_month

            monthly_spend_usd = await processor_cost_usd_month(redis_client)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("app.processor.model_policy", exc)

    decision = select_job_model(
        mode=mode,
        estimated_tokens=estimated_tokens,
        api_threshold_tokens=settings.PROCESSOR_API_THRESHOLD_TOKENS,
        monthly_spend_usd=monthly_spend_usd,
        cap_usd=Decimal(str(settings.PROCESSOR_MONTHLY_CAP_USD)),
        cap_fallback=settings.PROCESSOR_API_CAP_FALLBACK,
        default_local=default_local,
        api_model=api_model,
    )

    if decision.reason in _CAP_FALLBACK_REASONS:
        logger.warning(
            "processor.hybrid_cap_fallback monthly_spend_usd=%s cap_usd=%s "
            "fallback=%s reason=%s",
            monthly_spend_usd,
            settings.PROCESSOR_MONTHLY_CAP_USD,
            settings.PROCESSOR_API_CAP_FALLBACK,
            decision.reason,
        )

    return decision
