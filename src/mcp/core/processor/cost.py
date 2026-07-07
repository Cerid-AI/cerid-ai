# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cost estimation for LLM-backed jobs.

All monetary values are ``Decimal`` to avoid floating-point
accumulation errors in the rolling cost tracker. Pricing rows cover the
models Cerid ships: Anthropic claude-* family, OpenAI gpt-5, and the
zero-cost Ollama local path.

Unknown models raise ``ValueError`` — callers must either guard the call
or register the model in the pricing table. Silent fallback to a fake
price would corrupt cost projections.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Pre-execution cost projection for a single LLM-backed job.

    ``estimated_usd`` is a ``Decimal`` so multiple estimates can be
    summed without IEEE-754 drift. ``confidence`` reflects how well the
    job's token count is known before execution.
    """

    estimated_tokens_in: int
    estimated_tokens_out: int
    model: str
    estimated_usd: Decimal
    confidence: Confidence


# ---------------------------------------------------------------------------
# Pricing table
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _PricingRow:
    """Cost per 1 000 tokens (in/out) for a model."""

    usd_per_1k_in: Decimal
    usd_per_1k_out: Decimal


def _strip_openrouter_prefix(model: str) -> str:
    """Strip a leading ``openrouter/`` from a model id, if present.

    Mirrors ``core.utils.llm_client._strip_openrouter_prefix``; inlined
    here (rather than imported) to keep this module free of that
    module's heavier import surface.
    """
    if model.startswith("openrouter/"):
        return model[len("openrouter/"):]
    return model


class PricingTable:
    """Versioned map from canonical model identifier to per-token pricing.

    Keys use the ``provider/model-slug`` convention that Cerid's routing
    layer and cost tracker both reference, so this table doubles as a
    registry of supported billing identifiers.
    """

    _DEFAULT: dict[str, _PricingRow] = {
        # Anthropic — prices as of 2026-Q1
        "anthropic/claude-opus-4-7": _PricingRow(
            usd_per_1k_in=Decimal("0.015"),
            usd_per_1k_out=Decimal("0.075"),
        ),
        "anthropic/claude-sonnet-4-6": _PricingRow(
            usd_per_1k_in=Decimal("0.003"),
            usd_per_1k_out=Decimal("0.015"),
        ),
        # Alias: config/settings.py's CATEGORIZE_MODELS["pro"] ships as
        # "openrouter/anthropic/claude-sonnet-4.6" (dot, not hyphen). Same
        # model, same price — registered under both spellings so routed
        # model ids resolve without renaming the canonical hyphen key.
        "anthropic/claude-sonnet-4.6": _PricingRow(
            usd_per_1k_in=Decimal("0.003"),
            usd_per_1k_out=Decimal("0.015"),
        ),
        "anthropic/claude-haiku-4-5": _PricingRow(
            usd_per_1k_in=Decimal("0.00025"),
            usd_per_1k_out=Decimal("0.00125"),
        ),
        # OpenAI
        "openai/gpt-5": _PricingRow(
            usd_per_1k_in=Decimal("0.010"),
            usd_per_1k_out=Decimal("0.030"),
        ),
        # Local Ollama — zero marginal cost
        "ollama/local": _PricingRow(
            usd_per_1k_in=Decimal("0.000"),
            usd_per_1k_out=Decimal("0.000"),
        ),
    }

    def __init__(self, rows: dict[str, _PricingRow] | None = None) -> None:
        self._rows: dict[str, _PricingRow] = (
            rows if rows is not None else dict(self._DEFAULT)
        )

    def get_row(self, model: str) -> _PricingRow:
        """Return the pricing row for ``model`` or raise ``ValueError``.

        Falls back to a normalized lookup on a direct miss: the routing
        layer stores model ids with an ``openrouter/`` prefix (Bifrost
        convention) that this table's keys omit, and any ``:free`` model
        is zero-cost by definition regardless of whether it's registered.
        A direct hit always short-circuits, so normalization only ever
        turns a previously-raising lookup into a success — it never
        changes the result for an id already in the table.
        """
        try:
            return self._rows[model]
        except KeyError:
            pass

        normalized = _strip_openrouter_prefix(model)
        if normalized.endswith(":free"):
            return _PricingRow(usd_per_1k_in=Decimal("0"), usd_per_1k_out=Decimal("0"))

        try:
            return self._rows[normalized]
        except KeyError:
            raise ValueError(
                f"Unknown model '{model}' — register it in PricingTable before"
                " estimating cost."
            ) from None

    def registered_models(self) -> list[str]:
        """Return all registered model identifiers."""
        return list(self._rows.keys())


# Module-level default instance — sufficient for the vast majority of
# callers; tests or operators can construct a custom PricingTable.
_DEFAULT_TABLE = PricingTable()


def estimate(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    confidence: Confidence = "medium",
    table: PricingTable | None = None,
) -> CostEstimate:
    """Return a ``CostEstimate`` for the given model and token counts.

    Raises ``ValueError`` for unregistered models — callers must gate on
    ``model`` before calling or catch and handle the error explicitly.

    Parameters
    ----------
    model
        Canonical model id (``provider/slug``).
    tokens_in
        Estimated prompt-token count.
    tokens_out
        Estimated completion-token count.
    confidence
        Caller-supplied confidence in the token estimates.
    table
        Pricing table override; defaults to the module-level default.
    """
    t = table if table is not None else _DEFAULT_TABLE
    row = t.get_row(model)
    usd = (
        Decimal(tokens_in) * row.usd_per_1k_in / Decimal(1000)
        + Decimal(tokens_out) * row.usd_per_1k_out / Decimal(1000)
    )
    return CostEstimate(
        estimated_tokens_in=tokens_in,
        estimated_tokens_out=tokens_out,
        model=model,
        estimated_usd=usd,
        confidence=confidence,
    )
