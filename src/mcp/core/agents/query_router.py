# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query router — heuristic classifier for GraphRAG retrieval mode.

Workstream E Phase 4b.3. Decides per-query whether to use the local
mode (fine-grained chunk + entity-neighbourhood expansion) or the
global mode (community summaries via Phase 4b.2). The router is
consulted only when ``RETRIEVAL_MODE=auto`` (Phase 4b.4 default).

Heuristic v1 (fully deterministic, no LLM cost):

    GLOBAL  iff   word_count > 15
                  AND no quoted spans (single or double)
                  AND no proper nouns

    LOCAL   otherwise

Rationale:

  - Long queries with no quotes / no specific entities tend to be
    abstract or thematic ("How does monetary policy affect markets?")
    — community summaries answer those better than chunk retrieval.
  - Quoted spans signal exact-match intent ("\"WWDC 2024\" keynote")
    which favours chunk retrieval.
  - Proper nouns name specific entities ("Apple Inc."), which the
    local mode resolves through MENTIONS edges.

This module is layer-correct (pure function, no app/ imports). A
future v2 could swap in a tiny LLM router behind a feature flag —
the heuristic stays available as a deterministic fallback.
"""
from __future__ import annotations

import re
from typing import Literal

RetrievalMode = Literal["local_graphrag", "global_graphrag"]


_QUOTE_RE = re.compile(r'"[^"]+"|\'[^\']{2,}\'')

# Naive proper-noun detector: capitalised tokens of length ≥ 2 that
# don't sit at the very start of the query (where any sentence starts
# with a capital). Catches "Apple", "Federal Reserve", "BTC", etc.
# False positives are tolerable: a false-positive sends the query to
# local mode, which is the existing baseline behaviour.
#
# DUO138 false-positive note: dlint flags the inner `+` quantifier
# nested under the outer `*` group as catastrophic-backtracking-prone.
# It is not — the outer group's first character `[-/&]` cannot overlap
# with the leading `[A-Za-z0-9]+`, so the engine's no-match path bails
# in linear time. The regex bound on overall query length (input is
# always a user query of bounded size, never a long pasted blob) makes
# the worst case linear too.
_PROPER_NOUN_RE = re.compile(  # noqa: DUO138
    r"\b[A-Z][A-Za-z0-9]+(?:[-/&][A-Za-z0-9]+)*\b",
)

_WORD_COUNT_THRESHOLD = 15


def _strip_leading_capital(query: str) -> str:
    """Drop the first capitalised token if it's at position 0 — a sentence
    starter ("How does ...") is not a proper-noun signal.
    """
    stripped = query.lstrip()
    if not stripped:
        return ""
    first = stripped.split(None, 1)
    rest = first[1] if len(first) > 1 else ""
    return rest


def has_quoted_span(query: str) -> bool:
    return bool(_QUOTE_RE.search(query))


def has_proper_noun(query: str) -> bool:
    """Return True iff the query contains a non-leading capitalised token."""
    rest = _strip_leading_capital(query)
    return bool(_PROPER_NOUN_RE.search(rest))


def word_count(query: str) -> int:
    return len(query.split())


def route(query: str) -> RetrievalMode:
    """Decide which GraphRAG mode the query should use.

    Returns ``"global_graphrag"`` for thematic / abstract queries,
    ``"local_graphrag"`` otherwise. The caller (query_agent step-6
    branch) maps these onto the corresponding expansion paths; the
    legacy ``"baseline"`` mode is a separate config option set by the
    operator, not produced by the router.
    """
    if word_count(query) <= _WORD_COUNT_THRESHOLD:
        return "local_graphrag"
    if has_quoted_span(query):
        return "local_graphrag"
    if has_proper_noun(query):
        return "local_graphrag"
    return "global_graphrag"
