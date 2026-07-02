# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One canonical shape for external-augmentation evidence (QUERY/verify mode).

Before this, three incompatible shapes carried the same concepts with colliding
field names:

- ``DataSourceResult`` (app/data_sources): ``title / content / source_url /
  source_name / confidence``
- ``WebSearchResult`` (utils/web_search): ``title / url / snippet / score /
  published_date``
- the ad-hoc ``authoritative_sources`` dict (hallucination/authoritative_verify):
  ``source / content / source_url / nli_entailment / nli_contradiction /
  data_freshness``

``ExternalEvidence`` absorbs those collisions via :meth:`from_mapping`, which
normalises whichever spelling a producer used. Design notes:

- **Numeric fields are Optional** (``None`` = the producer supplied no value).
  Relevance defaults stay a *consumer* concern so migrating a call site never
  silently changes its historical fallback (e.g. crag's ``confidence`` default
  of 0.8, applied only when the key is absent, not when it is a real ``0.0``).
- **NLI scores are a separate slot**, never folded into ``relevance``: they are
  classifier probabilities on a different scale than calibrated 0-1 relevance.
- **The external discount is NOT applied here** — it stays a single consumer-side
  step (``EXTERNAL_SOURCE_RELEVANCE_DISCOUNT``), applied once at merge time.

POLL/ingest mode (``SourceArtifactEvent``) is deliberately NOT modelled here — it
carries no url/content/relevance; it is a cursor notification, not evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# All date-ish spellings seen across producers, in precedence order. Note: no
# QUERY-mode source currently populates any of these, so ``published_date``
# resolves to ``None`` in practice today — the field exists so a producer that
# starts emitting provenance dates flows through without a schema change.
_DATE_KEYS = (
    "published_date",
    "published_at",
    "data_freshness",
    "last_updated",
    "published",
    "created_at",
    "retrieved_at",
)
# Placeholder freshness values that mean "no real date" → normalise to None.
_EMPTY_DATES = ("", "unknown")


def _first_str(d: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if v:
            return str(v)
    return ""


def _first_float(d: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return None


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ExternalEvidence:
    """Normalised external-source evidence. Immutable; build via ``from_mapping``."""

    title: str = ""
    content: str = ""
    url: str = ""
    source_name: str = ""
    relevance: float | None = None  # calibrated 0-1; None = producer gave none
    published_date: str | None = None
    nli_entailment: float | None = None  # NLI prob — separate scale from relevance
    nli_contradiction: float | None = None
    guid: str | None = None  # ingest provenance, when present

    @classmethod
    def from_mapping(
        cls,
        d: Mapping[str, Any],
        *,
        nli_entailment: float | None = None,
        nli_contradiction: float | None = None,
    ) -> ExternalEvidence:
        """Normalise a producer dict (any of the three shapes) into evidence.

        Explicit ``nli_*`` kwargs win over dict-carried NLI scores so a caller
        that just computed entailment can pass it directly.
        """
        published = _first_str(d, _DATE_KEYS)
        return cls(
            title=_first_str(d, ("title", "source_name", "source")),
            content=_first_str(d, ("content", "snippet")),
            url=_first_str(d, ("url", "source_url")),
            source_name=_first_str(d, ("source_name", "source")),
            relevance=_first_float(d, ("relevance", "confidence", "score")),
            published_date=None if published in _EMPTY_DATES else published,
            nli_entailment=(
                nli_entailment
                if nli_entailment is not None
                else _opt_float(d.get("nli_entailment"))
            ),
            nli_contradiction=(
                nli_contradiction
                if nli_contradiction is not None
                else _opt_float(d.get("nli_contradiction"))
            ),
            guid=(str(d["guid"]) if d.get("guid") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical serialisation. Optional fields are omitted when unset."""
        out: dict[str, Any] = {
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "source_name": self.source_name,
        }
        if self.relevance is not None:
            out["relevance"] = self.relevance
        if self.published_date is not None:
            out["published_date"] = self.published_date
        if self.nli_entailment is not None:
            out["nli_entailment"] = self.nli_entailment
        if self.nli_contradiction is not None:
            out["nli_contradiction"] = self.nli_contradiction
        if self.guid is not None:
            out["guid"] = self.guid
        return out

    def to_authoritative_dict(self) -> dict[str, Any]:
        """Emit the exact ``authoritative_sources`` shape (byte-parity adoption).

        Preserves the legacy defaults: ``source``/``data_freshness`` fall back to
        ``"unknown"`` and NLI scores to ``0.0`` so consumers see no change.
        """
        return {
            "source": self.source_name or "unknown",
            "content": self.content[:200],
            "source_url": self.url,
            "nli_entailment": self.nli_entailment if self.nli_entailment is not None else 0.0,
            "nli_contradiction": (
                self.nli_contradiction if self.nli_contradiction is not None else 0.0
            ),
            "data_freshness": self.published_date or "unknown",
        }
