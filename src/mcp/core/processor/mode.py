# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Processor mode contract (docs/BACKGROUND_JOBS.md §9).

Pure decision logic for ``PROCESSOR_MODE`` — no Redis, no app-layer
imports. Callers (``app.processor.model_policy``) thread in the current
monthly spend and settings values; this module only decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ProcessorMode = Literal["local", "hybrid", "disabled"]

_VALID_MODES: frozenset[str] = frozenset({"local", "hybrid", "disabled"})


def resolve_processor_mode(raw: str | None) -> ProcessorMode:
    """Normalize a raw ``PROCESSOR_MODE`` value. Unknown/None falls back to ``local``."""
    if raw is None:
        return "local"
    normalized = raw.strip().lower()
    if normalized in _VALID_MODES:
        return normalized  # type: ignore[return-value]
    return "local"


def processor_is_disabled(mode: ProcessorMode) -> bool:
    """Return True when the worker must not dequeue any jobs."""
    return mode == "disabled"


@dataclass(frozen=True, slots=True)
class ModelDecision:
    """Outcome of routing a job to a local or API-tier model."""

    model: str | None
    hold: bool
    reason: str


def select_job_model(
    *,
    mode: ProcessorMode,
    estimated_tokens: int,
    api_threshold_tokens: int,
    monthly_spend_usd: Decimal,
    cap_usd: Decimal,
    cap_fallback: str,
    default_local: str,
    api_model: str,
) -> ModelDecision:
    """Decide which model a job should run on given the processor mode.

    Semantics (exactly per docs/BACKGROUND_JOBS.md §9):

    - ``local`` / ``disabled`` → always ``default_local``.
    - ``hybrid`` & ``estimated_tokens <= api_threshold_tokens`` → ``default_local``.
    - ``hybrid`` & ``estimated_tokens > api_threshold_tokens``:
        - ``monthly_spend_usd < cap_usd`` → ``api_model``.
        - otherwise, fall back per ``cap_fallback``:
            - ``"hold"`` → ``model=None, hold=True`` (job stays queued).
            - anything else (``"local"``) → ``default_local``.
    """
    if mode in ("local", "disabled"):
        return ModelDecision(
            model=default_local, hold=False, reason=f"mode_{mode}"
        )

    # mode == "hybrid"
    if estimated_tokens <= api_threshold_tokens:
        return ModelDecision(
            model=default_local, hold=False, reason="hybrid_under_threshold"
        )

    if monthly_spend_usd < cap_usd:
        return ModelDecision(
            model=api_model, hold=False, reason="hybrid_api_routed"
        )

    if cap_fallback == "hold":
        return ModelDecision(model=None, hold=True, reason="hybrid_cap_hold")
    return ModelDecision(
        model=default_local, hold=False, reason="hybrid_cap_fallback_local"
    )
