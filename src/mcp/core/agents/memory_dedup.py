# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Retroactive near-duplicate detection for stored memories (UX-17).

Write-time consolidation (``memory_consolidation.classify_memory``) only
sees each memory as it arrives, needs the LLM to be responsive, and was
observed missing rephrasings: "SOL price down 1.38%" and "SOL is down
1.23% today" were both stored from one conversation, and 57/61 memories
were micro-facts of that shape. This module is the deterministic
maintenance pass over what already exists — pure text similarity, no LLM
calls (bf-f3 default: background work must not contend with interactive
inference).

Numbers are collapsed before comparison on purpose: two snapshots of the
same fluctuating metric phrased alike ARE the same memory, and the newer
one is the one worth keeping. Distinct facts differ in their words, not
just their digits, and stay apart.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# Similarity floor for "same memory, different phrasing". Tuned against the
# observed SOL pair (≈0.93 after normalisation) while distinct facts about
# the same subject score well below (≈0.6-0.7).
DEDUP_SIMILARITY_THRESHOLD = 0.90

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_NON_WORD_RE = re.compile(r"[^\w\s#]+")
_WS_RE = re.compile(r"\s+")


def normalize_memory_text(text: str) -> str:
    """Lowercase, collapse every number to ``#``, strip punctuation runs."""
    lowered = (text or "").lower()
    no_numbers = _NUMBER_RE.sub("#", lowered)
    no_punct = _NON_WORD_RE.sub(" ", no_numbers)
    return _WS_RE.sub(" ", no_punct).strip()


# Token containment only applies when the smaller memory carries enough
# tokens to be a claim of its own — below this it would merge trivially.
_MIN_CONTAINMENT_TOKENS = 4

# Negation markers that flip a claim's meaning. When one text carries such a
# token and the other does not, they are opposing claims, never rephrasings —
# containment treats "not" as filler and the character ratio also clears the
# threshold for "X" vs "not X", so the guard is a hard veto, not a discount.
# Contraction stems appear because normalisation splits "isn't" into
# "isn t"; a stem in the symmetric difference means the texts genuinely
# differ by that word, so vetoing the merge is safe even for homographs.
_NEGATION_TOKENS = frozenset({
    "not", "no", "never", "cannot", "without", "n't",
    "isn", "aren", "wasn", "weren", "don", "doesn", "didn",
    "won", "wouldn", "couldn", "shouldn", "hasn", "haven", "hadn",
})


def memory_similarity(a: str, b: str) -> float:
    """Similarity of two memory texts after normalisation, in [0, 1].

    Two measures, take the max:

    * character-level ``SequenceMatcher`` ratio — catches reorderings and
      small edits;
    * token containment (shared tokens / smaller set) — catches the
      observed rephrasing shape, where one phrasing is the other plus a
      couple of filler words ("SOL price down #" ⊂ "SOL price is down #
      today"). Gated on the smaller memory having enough tokens to be a
      claim of its own.

    A claim and its negation score 0.0 regardless of either measure: a
    negation token present in one text but not the other is a meaning
    flip, not a phrasing difference.
    """
    na, nb = normalize_memory_text(a), normalize_memory_text(b)
    if not na or not nb:
        return 0.0

    ta, tb = set(na.split()), set(nb.split())
    if (ta ^ tb) & _NEGATION_TOKENS:
        return 0.0

    ratio = SequenceMatcher(None, na, nb).ratio()
    smaller = min(len(ta), len(tb))
    if smaller >= _MIN_CONTAINMENT_TOKENS:
        containment = len(ta & tb) / smaller
        return max(ratio, containment)
    return ratio


def find_duplicate_groups(
    memories: list[dict[str, Any]],
    *,
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
) -> list[list[dict[str, Any]]]:
    """Cluster near-duplicate memories; returns groups of 2+ members.

    Each input dict needs ``id``, ``text`` and ``created_at`` (ISO string).
    Within a group the FIRST member is the keeper — the newest by
    ``created_at`` — and every later member is a duplicate of it. Greedy
    single-pass clustering against each group's keeper: quadratic worst
    case, fine at the tens-to-hundreds scale of a memory store.
    """
    ordered = sorted(
        memories, key=lambda m: str(m.get("created_at") or ""), reverse=True,
    )
    groups: list[list[dict[str, Any]]] = []
    for mem in ordered:
        text = str(mem.get("text") or "")
        placed = False
        for group in groups:
            keeper_text = str(group[0].get("text") or "")
            if memory_similarity(text, keeper_text) >= threshold:
                group.append(mem)
                placed = True
                break
        if not placed:
            groups.append([mem])
    return [g for g in groups if len(g) > 1]
