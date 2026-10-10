# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The web client's grounded chat turn, reproduced for the benchmark.

``/chat/stream`` forwards the messages it is given and retrieves nothing; the
web client grounds a turn itself, injecting the retrieved chunks as a system
message ahead of the question (``src/web/src/hooks/use-chat-send.ts``). An
answer requested with the bare question is the model's general knowledge, so
scoring it against retrieved context measures nothing the product does.

Mirrors the client's auto-inject defaults (a relevance floor relative to the
best hit, at most three chunks, Jaccard dedup), its preamble and sentinel
(``src/web/src/lib/rag-prompt.ts``) and its ``<document>`` blocks
(``src/web/src/lib/kb-utils.ts``). The eval container mounts only
``tests/beta``, so these are copies; ``scripts/tests/test_beta_grounding_parity.py``
holds them to the TypeScript.
"""

from __future__ import annotations

from typing import Any

KB_CONTEXT_SENTINEL = "<!--cerid:kb-context-->"
RAG_SYSTEM_PREAMBLE = KB_CONTEXT_SENTINEL + "\n" + (
    'The user has a personal knowledge base. Below are documents retrieved for this '
    'conversation; each is tagged with its source. Rules: (1) When the documents answer '
    'the question, ground your answer in them and cite specifics. (2) Distinguish clearly '
    'between facts from these documents and your general knowledge. (3) For '
    'time-sensitive values (prices, versions, schedules), treat the documents as '
    "point-in-time records — qualify with the document's date if shown, otherwise present "
    'the value as a recorded (possibly outdated) value, never as current; suggest '
    'checking a live source when currency matters. When a document shows earlier versions '
    'in a <history> block, answer from its current value and mention an earlier one when it '
    'matters to the question ("this is Y; it used to be X"); otherwise, when documents '
    "conflict about the same fact, trust the most recently dated one. (4) If the documents don't cover the "
    'question, say so plainly, then answer from general knowledge if you can, labeled as '
    'such. A clear "your knowledge base doesn\'t cover this" is better than a guess. (5) '
    'For analytical questions — counting or combining facts across documents, date '
    "arithmetic (how long between events, which came first), or applying the user's "
    'stated preferences — reason step by step across the documents and DERIVE the answer; '
    "don't refuse just because no single document states it outright. Only say you can't "
    'answer when the underlying facts are genuinely absent.'
)

AUTO_INJECT_THRESHOLD = 0.15
AUTO_INJECT_MAX = 3
JACCARD_DEDUP_THRESHOLD = 0.7


def _jaccard(a: str, b: str) -> float:
    words_a, words_b = set(a.lower().split()), set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    inter = len(words_a & words_b)
    union = len(words_a) + len(words_b) - inter
    return inter / union if union else 0.0


def format_document(source: dict[str, Any]) -> str:
    attrs: list[str] = []
    if source.get("artifact_id"):
        attrs.append(f'id="{source["artifact_id"]}"')
    if source.get("domain"):
        attrs.append(f'domain="{source["domain"]}"')
    if source.get("sub_category"):
        attrs.append(f'category="{source["sub_category"]}"')
    if source.get("filename"):
        attrs.append(f'source="{source["filename"]}"')
    if source.get("chunk_index") is not None:
        attrs.append(f'chunk="{source["chunk_index"]}"')
    if source.get("relevance") is not None:
        attrs.append(f'relevance="{source["relevance"]:.2f}"')
    if source.get("source_type"):
        attrs.append(f'type="{source["source_type"]}"')
    created = source.get("created_at") or ""
    if len(created) >= 10 and created[4] == "-" and created[7] == "-" and created[:4].isdigit():
        attrs.append(f'date="{created[:10]}"')
    attr_str = (" " + " ".join(attrs)) if attrs else ""
    return f"<document{attr_str}>\n{source.get('content', '')}{history_block(source.get('history'))}\n</document>"


def history_block(history: list[dict[str, Any]] | None) -> str:
    """Earlier versions of a result, as the client renders them (``historyBlock``)."""
    if not history:
        return ""
    lines = []
    for entry in history:
        until = str(entry.get("valid_to") or "")
        date = until[:10] if len(until) >= 10 and until[4] == "-" and until[7] == "-" else "unknown"
        lines.append(f"- until {date}: {entry.get('value', '')}")
    return "\n<history>\n" + "\n".join(lines) + "\n</history>"


def grounded_turn(sources: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The system message the client would send for these retrieval results,
    and the chunk texts it carries (what the judge should score against).
    ``(None, [])`` when nothing clears the floor: the client sends no system
    message then."""
    top = max((s.get("relevance") or 0.0 for s in sources), default=0.0)
    floor = AUTO_INJECT_THRESHOLD * top if top > 0 else 0.0
    picked = [s for s in sources if (s.get("relevance") or 0.0) >= floor][:AUTO_INJECT_MAX]
    kept: list[dict[str, Any]] = []
    for s in picked:
        if not any(_jaccard(k.get("content", ""), s.get("content", "")) >= JACCARD_DEDUP_THRESHOLD for k in kept):
            kept.append(s)
    if not kept:
        return None, []
    blocks = "\n\n".join(format_document(s) for s in kept)
    return f"{RAG_SYSTEM_PREAMBLE}\n\n{blocks}", [s.get("content", "") for s in kept]
