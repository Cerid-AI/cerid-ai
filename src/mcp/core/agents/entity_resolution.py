# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Alias-aware entity canonicalization for the GraphRAG layer.

Task 2.2 — root-cause fix for the 90% single-mention tail in the entity
graph.  Three cheapest-first tiers, each independent and unit-testable:

  Tier A — curated alias table (deterministic, zero cost)
  Tier B — string normalization: honorifics / middle initials / legal suffixes
  Tier C — opt-in embedding nearest-canonical (requires embed= callable
            AND settings.ENTITY_RESOLUTION_EMBED must be True)

Layer-correct: this module stays in core/ and takes embed as a parameter.
It does NOT import from app/ or any transport/driver layer.
Importing from config.* is allowed (plain os.getenv reads).

Public entry point
------------------
    resolve_canonical(name, entity_type, *, embed=None, existing=None) -> str

Returns a stable canonical id: ``{type_lower}:{slug}``.  Falls back to
today's ``{type}:{slug}`` when no tier merges.  Never merges across
different entity_type values.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, NamedTuple

import config.settings as _settings
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.entity_resolution")

# ---------------------------------------------------------------------------
# Slug helper (mirrors entity_extraction.canonical_id's slug step)
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-")


# ---------------------------------------------------------------------------
# Tier A — curated alias table
#
# Structure: {entity_type: {normalized_alias_slug: canonical_slug}}
#
# The alias lookup is scoped to entity_type: an alias for "fed" under "ORG"
# will NOT fire when entity_type is "ASSET" (where "FED" is a ticker symbol).
# Type isolation is enforced HERE in the table structure — not delegated to
# the caller. The type-prefix is added by resolve_canonical.
#
# SEED clusters (add more as corpus analysis reveals top collision groups).
# ---------------------------------------------------------------------------

_ALIAS_TABLE: dict[str, dict[str, str]] = {
    "ORG": {
        # US Federal Reserve cluster
        "federal-reserve": "federal-reserve",
        "fed": "federal-reserve",
        "the-fed": "federal-reserve",
        "fomc": "federal-reserve",
        "federal-open-market-committee": "federal-reserve",
        # US Treasury
        "treasury": "us-treasury",
        "us-treasury": "us-treasury",
        "united-states-treasury": "us-treasury",
        "department-of-the-treasury": "us-treasury",
        # SEC
        "sec": "sec",
        "securities-and-exchange-commission": "sec",
        "us-sec": "sec",
        # IMF
        "imf": "imf",
        "international-monetary-fund": "imf",
        # ECB
        "ecb": "ecb",
        "european-central-bank": "ecb",
        # BIS
        "bis": "bis",
        "bank-for-international-settlements": "bis",
    },
    "INDEX": {
        # S&P 500 / market index aliases (commonly mined in finance corpora)
        "s-p-500": "sp500",
        "sp500": "sp500",
        "s-p": "sp500",
        "spx": "sp500",
        # Dow Jones
        "dow-jones": "dow-jones",
        "djia": "dow-jones",
        "dow": "dow-jones",
        # Nasdaq
        "nasdaq": "nasdaq",
        "nasdaq-composite": "nasdaq",
        "ixic": "nasdaq",
    },
}


def _tier_a(slug: str, entity_type: str) -> str | None:
    """Return canonical slug from alias table for the given entity_type, or None.

    The lookup is scoped to entity_type so that the same slug (e.g. "fed")
    resolves differently — or not at all — for ASSET vs ORG.
    """
    type_table = _ALIAS_TABLE.get(entity_type, {})
    return type_table.get(slug)


# ---------------------------------------------------------------------------
# Tier B — string normalization
#
# Applied before slugifying.  Order matters: strip honorific first so the
# remainder is "Firstname [Initial.] Lastname", then strip middle initials,
# then strip legal suffix.
# ---------------------------------------------------------------------------

# Honorifics: matched at start of string, case-insensitive.
_HONORIFIC_RE = re.compile(
    r"^(?:Mr\.?|Mrs\.?|Ms\.?|Miss|Dr\.?|Prof\.?|Sir|Dame|Rev\.?|Gen\.?|Adm\.?)\s+",
    re.IGNORECASE,
)

# Middle initials: one or more " X." patterns (single letter + period).
# Applied iteratively via re.sub so ALL initials are stripped.
# E.g. "John A. B. Smith" → "John Smith"; "Elon R. Musk" → "Elon Musk".
_MIDDLE_INITIAL_RE = re.compile(r"\s+[A-Za-z]\.")

# Legal suffixes for organizations: matched at end of string (with optional dot).
_LEGAL_SUFFIX_RE = re.compile(
    r",?\s+(?:Inc|LLC|L\.L\.C|Corp|Corporation|Ltd|Limited|PLC|P\.L\.C|"
    r"Co|Company|LLP|LP|L\.P|N\.A|NA|S\.A|SA|GmbH|AG|B\.V|NV|Pty\.?\s*Ltd)\.?$",
    re.IGNORECASE,
)

# Stop-words/articles that are meaningless as a bare canonical name.
_STOP_WORDS = {"the", "a", "an"}


def _normalize_name(name: str, entity_type: str) -> str:
    """Strip honorifics, middle initials (PERSON), and legal suffixes (ORG)."""
    normalized = name.strip()
    if entity_type == "PERSON":
        normalized = _HONORIFIC_RE.sub("", normalized).strip()
        # Strip ALL middle initials iteratively until none remain.
        normalized = _MIDDLE_INITIAL_RE.sub("", normalized).strip()
    elif entity_type == "ORG":
        stripped = _LEGAL_SUFFIX_RE.sub("", normalized).strip()
        # Guard: do not strip if the result is empty or a bare stop-word/article.
        if stripped and stripped.lower() not in _STOP_WORDS:
            normalized = stripped
    return normalized


def _tier_b(name: str, entity_type: str) -> str:
    """Return slug after normalization (may equal the raw slug if nothing stripped)."""
    normalized = _normalize_name(name, entity_type)
    return _slugify(normalized)


# ---------------------------------------------------------------------------
# Tier C — opt-in embedding nearest-canonical
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _tier_c(
    name: str,
    entity_type: str,
    embed: Callable[[str], list[float]],
    existing: dict[str, list[str]],
    threshold: float,
) -> str | None:
    """Return canonical slug of the nearest existing canonical, or None."""
    candidates = existing.get(entity_type, [])
    if not candidates:
        return None
    vec = embed(name)
    best_sim = -1.0
    best_slug: str | None = None
    for candidate in candidates:
        cand_vec = embed(candidate)
        sim = _cosine(vec, cand_vec)
        if sim > best_sim:
            best_sim = sim
            best_slug = _slugify(_normalize_name(candidate, entity_type))
    if best_sim >= threshold and best_slug:
        return best_slug
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_canonical(
    name: str,
    entity_type: str,
    *,
    embed: Callable[[str], list[float]] | None = None,
    existing: dict[str, list[str]] | None = None,
) -> str:
    """Resolve (name, entity_type) to a stable canonical id.

    Tiers run cheapest-first:

    A  Alias table lookup on the raw slug (type-scoped: aliases for ORG
       will NOT fire for ASSET, etc.).
    B  String normalization (honorifics / initials / legal suffixes) then slug.
       Legal suffix stripping is guarded: if stripping leaves an empty string
       or a bare article ("the", "a", "an"), the original name is kept.
    C  Embedding nearest-canonical — runs ONLY when BOTH conditions hold:
       (1) ``embed`` callable is not None, AND
       (2) ``settings.ENTITY_RESOLUTION_EMBED`` is True.
       The reprocess job (Task 2.4) sets the flag and passes ``embed``.
       Tests that need Tier C should enable the flag via monkeypatch.

    Falls back to ``{type_lower}:{raw_slug}`` when nothing merges.
    NEVER merges across different entity_type values.

    Parameters
    ----------
    name:
        Raw surface form ("the Fed", "Elon R. Musk", "Apple Inc.").
    entity_type:
        Uppercase entity type string ("ORG", "PERSON", "ASSET", …).
    embed:
        Optional callable ``(name: str) -> list[float]`` for Tier C.
        Tier C runs only when this is not None AND ENTITY_RESOLUTION_EMBED
        is True.
    existing:
        Mapping of entity_type → list of canonical surface forms already in
        the graph.  Required for Tier C comparison; ignored if embed is None.
    """
    type_prefix = entity_type.lower()
    raw_slug = _slugify(name)

    if not raw_slug:
        return f"{type_prefix}:"

    # Tier A — alias table (type-scoped)
    canonical_slug = _tier_a(raw_slug, entity_type)
    if canonical_slug:
        return f"{type_prefix}:{canonical_slug}"

    # Tier B — normalization
    norm_slug = _tier_b(name, entity_type)
    # Check alias table again on the normalized slug (e.g. "Federal Reserve Inc." → "federal-reserve")
    canonical_slug = _tier_a(norm_slug, entity_type)
    if canonical_slug:
        return f"{type_prefix}:{canonical_slug}"

    if norm_slug != raw_slug:
        # Normalization changed the slug — use it as the canonical
        return f"{type_prefix}:{norm_slug}"

    # Tier C — embedding (gated by ENTITY_RESOLUTION_EMBED setting)
    # Read from the module at call time so monkeypatch works in tests.
    if embed is not None and _settings.ENTITY_RESOLUTION_EMBED and existing:
        result = _tier_c(name, entity_type, embed, existing, _settings.ENTITY_RESOLUTION_SIM)
        if result:
            return f"{type_prefix}:{result}"

    # Fallback — bare slug (same as today's canonical_id)
    return f"{type_prefix}:{raw_slug}"


# ===========================================================================
# Phase 4.2 — embedding-merge candidate generation + LLM adjudication
# ===========================================================================
#
# The Tier-C resolve_canonical path above is the *ingest-time* nearest-canonical
# hook. This section is the *sweep-time* resolution engine: given the whole
# entity set with precomputed embeddings, it emits high-similarity PAIRS and
# routes them into three bands (see config ENTITY_MERGE_* for the thresholds):
#
#   sim >= AUTO_SIM        → auto-merge (structural certainty; no LLM cost)
#   ADJUDICATE_SIM <= sim
#                < AUTO_SIM → LLM adjudication (merge/keep), bounded per run
#   sim <  ADJUDICATE_SIM   → no action (SIMILAR_TO connectivity already links
#                             them; this is NOT a merge candidate)
#
# Cross-type merges are impossible by construction: pairing is scoped inside a
# single entity_type group.

# Minimum group members before any pairing is worth doing.
_MIN_PAIR_GROUP = 2
# Token budget for one adjudication batch — a compact merge/keep verdict list.
_ADJUDICATION_MAX_TOKENS = 400
# Truncate the per-entity context snippet so a batch prompt stays compact.
_CONTEXT_SNIPPET_CHARS = 200


class MergePair(NamedTuple):
    """A candidate pair of entities that resolution may collapse.

    ``band`` is ``"auto"`` (>= AUTO_SIM) or ``"adjudicate"`` (in the borderline
    band). ``a_id`` sorts lexicographically before ``b_id`` so a pair has one
    canonical form (no (a,b)/(b,a) duplication).
    """

    a_id: str
    b_id: str
    a_name: str
    b_name: str
    entity_type: str
    similarity: float
    band: str


def _normalize_vec(vec: list[float]) -> list[float]:
    mag = sum(x * x for x in vec) ** 0.5
    if mag == 0:
        return vec
    return [x / mag for x in vec]


def generate_merge_candidates(
    entities: list[dict[str, Any]],
    *,
    auto_threshold: float | None = None,
    adjudicate_floor: float | None = None,
) -> list[MergePair]:
    """Pair entities by embedding cosine, bucketed into auto / adjudicate bands.

    ``entities`` is a list of dicts with ``canonical_id``, ``name``,
    ``entity_type`` and ``embedding`` (``list[float]``; entities without an
    embedding are skipped). Pairing is scoped per ``entity_type`` (never
    cross-type). Pairs below ``adjudicate_floor`` are dropped (SIMILAR_TO
    connectivity covers them). Returns pairs sorted by similarity descending so
    a bounded adjudication cap consumes the most-confident borderline pairs
    first.
    """
    import numpy as np  # noqa: PLC0415 — heavy import deferred to call time

    auto = auto_threshold if auto_threshold is not None else _settings.ENTITY_MERGE_AUTO_SIM
    floor = (
        adjudicate_floor
        if adjudicate_floor is not None
        else _settings.ENTITY_MERGE_ADJUDICATE_SIM
    )

    by_type: dict[str, list[dict[str, Any]]] = {}
    for ent in entities:
        emb = ent.get("embedding")
        if not emb:
            continue
        by_type.setdefault(ent["entity_type"], []).append(ent)

    pairs: list[MergePair] = []
    for etype, group in by_type.items():
        if len(group) < _MIN_PAIR_GROUP:
            continue
        matrix = np.asarray(
            [_normalize_vec(list(ent["embedding"])) for ent in group],
            dtype=np.float32,
        )
        sims = matrix @ matrix.T
        rows, cols = np.triu_indices(len(group), k=1)
        flat = sims[rows, cols]
        keep = np.where(flat >= floor)[0]
        for k in keep:
            i = int(rows[k])
            j = int(cols[k])
            sim = float(flat[k])
            a, b = group[i], group[j]
            # Canonical pair order: lexicographically smaller id first.
            if a["canonical_id"] > b["canonical_id"]:
                a, b = b, a
            band = "auto" if sim >= auto else "adjudicate"
            pairs.append(
                MergePair(
                    a_id=a["canonical_id"],
                    b_id=b["canonical_id"],
                    a_name=a["name"],
                    b_name=b["name"],
                    entity_type=etype,
                    similarity=sim,
                    band=band,
                )
            )

    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs


def build_entity_merge_adjudication_messages(
    pairs: list[MergePair],
    contexts: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Compact merge/keep prompt for a batch of borderline pairs.

    The model sees each pair's two surface names plus (optional) short context
    snippets and returns a JSON verdict list. Deliberately terse: this runs
    once per borderline pair and cost is bounded per run.
    """
    ctx = contexts or {}
    lines: list[str] = []
    for idx, pair in enumerate(pairs):
        a_ctx = ctx.get(pair.a_id, "")
        b_ctx = ctx.get(pair.b_id, "")
        a_suffix = f" ({a_ctx[:_CONTEXT_SNIPPET_CHARS]})" if a_ctx else ""
        b_suffix = f" ({b_ctx[:_CONTEXT_SNIPPET_CHARS]})" if b_ctx else ""
        lines.append(
            f'{idx}. type={pair.entity_type}: '
            f'"{pair.a_name}"{a_suffix} vs "{pair.b_name}"{b_suffix}'
        )
    system = (
        "You de-duplicate a knowledge graph. For each numbered pair decide "
        "whether the two names refer to the SAME real-world entity. Only answer "
        '"merge" when you are confident they are the same; otherwise "keep". '
        'Reply with JSON only: {"results": [{"index": <int>, "decision": '
        '"merge"|"keep"}]}.'
    )
    user = "Pairs:\n" + "\n".join(lines)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_adjudication(raw: str, count: int) -> dict[int, str]:
    """Parse the model's JSON verdict list into {index: decision}.

    Conservative: any index not explicitly returned as ``"merge"`` defaults to
    ``"keep"`` (a parse failure never triggers a merge).
    """
    decisions: dict[int, str] = {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1 :]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        log_swallowed_error(
            "core.agents.entity_resolution._parse_adjudication", exc,
            context={"raw_prefix": raw[:_CONTEXT_SNIPPET_CHARS]},
        )
        return decisions
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        return decisions
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        decision = item.get("decision")
        if isinstance(idx, int) and 0 <= idx < count and decision == "merge":
            decisions[idx] = "merge"
    return decisions


async def adjudicate_merge_pairs(
    pairs: list[MergePair],
    *,
    contexts: dict[str, str] | None = None,
    llm_call: Callable[..., Awaitable[str]] | None = None,
    max_pairs: int | None = None,
    batch_size: int | None = None,
) -> list[MergePair]:
    """Send borderline pairs to the internal LLM; return the confirmed merges.

    Bounded per run: at most ``max_pairs`` (default
    ``ENTITY_MERGE_ADJUDICATION_MAX_PAIRS``) pairs are adjudicated, in batches
    of ``batch_size`` (default ``ENTITY_MERGE_ADJUDICATION_BATCH``). A batch
    that errors or fails to parse yields NO merges (conservative). ``llm_call``
    defaults to :func:`core.utils.internal_llm.call_internal_llm` and is
    injectable for tests.
    """
    if not pairs:
        return []

    if llm_call is None:
        from core.utils.internal_llm import call_internal_llm  # noqa: PLC0415
        llm_call = call_internal_llm

    cap = (
        max_pairs
        if max_pairs is not None
        else _settings.ENTITY_MERGE_ADJUDICATION_MAX_PAIRS
    )
    batch = (
        batch_size
        if batch_size is not None
        else _settings.ENTITY_MERGE_ADJUDICATION_BATCH
    )
    batch = max(1, int(batch))
    bounded = list(pairs)[: max(0, int(cap))]

    confirmed: list[MergePair] = []
    for start in range(0, len(bounded), batch):
        window = bounded[start : start + batch]
        messages = build_entity_merge_adjudication_messages(window, contexts)
        try:
            raw = await llm_call(
                messages,
                stage="entity_merge_adjudication",
                temperature=0.0,
                max_tokens=_ADJUDICATION_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the sweep
            log_swallowed_error(
                "core.agents.entity_resolution.adjudicate_merge_pairs", exc,
                context={"batch_start": start},
            )
            continue
        decisions = _parse_adjudication(raw, len(window))
        for i, pair in enumerate(window):
            if decisions.get(i) == "merge":
                confirmed.append(pair)
    return confirmed
