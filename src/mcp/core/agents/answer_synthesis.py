# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Answer-synthesis mode selection + prompt construction.

Cerid's answer layer was uniformly *extractive* ("answer from the context, be
direct, say so if it's insufficient") — correct for fact lookup, but it makes
the reader ABSTAIN on analytical questions whose answer must be *derived* from
evidence that is present but not literal: counting across sessions, date
arithmetic / ordering, or applying a stored preference. Benchmarking surfaced
this as the dominant failure mode (the reader answered "I don't know" with the
relevant dated evidence already in context).

This module selects an answer MODE from the question and builds a
reasoning-enabled prompt for the analytical modes, while preserving the concise
extractive prompt for fact lookup (so single-hop accuracy does not regress).

It is deliberately a pure ``core`` primitive (no ``app`` imports, no I/O) so the
SAME synthesis behaviour is shared by the production reader
(``app.mcp_tools.retrieval.pkb_answer_with_citations``) and the LongMemEval eval
pipeline — the benchmark then measures the capability the product ships, not an
eval-only prompt.

References (technique provenance):
- LongMemEval (Wu et al., ICLR 2025): reader strength + explicit step-by-step
  reasoning drive temporal / multi-session accuracy; extractive readers abstain.
- Graphiti (Zep): surface each fact's date to the reader and let it reason over
  the timeline; prefer the most recent on conflict.
- mem0 V3: apply user preferences explicitly rather than leaving them implicit.
"""
from __future__ import annotations

import re
from enum import Enum


class AnswerMode(str, Enum):
    """How the reader should turn retrieved memories into an answer."""

    EXTRACTIVE = "extractive"     # single-hop fact lookup (default, unchanged)
    TEMPORAL = "temporal"         # date arithmetic / ordering / duration
    AGGREGATION = "aggregation"   # count / combine across multiple memories
    PREFERENCE = "preference"     # apply a stored user preference


# Oracle path: when a caller knows the question type (the LongMemEval eval does),
# map it directly. Production callers pass ``question_type=None`` and we classify
# from the question text below.
_TYPE_TO_MODE: dict[str, AnswerMode] = {
    "temporal-reasoning": AnswerMode.TEMPORAL,
    "multi-session": AnswerMode.AGGREGATION,
    "single-session-preference": AnswerMode.PREFERENCE,
}

# Order matters in classify_answer_mode: temporal is checked before aggregation
# because "how many days" is a duration question, not a count.
_TEMPORAL_RE = re.compile(
    r"\b(how long|how many (?:days|weeks|months|years)|when did|when was|"
    r"before or after|how much time|since when|what year|what month|"
    r"how long ago|days? (?:before|after|between)|earlier or later)\b",
    re.I,
)
_AGGREGATION_RE = re.compile(
    r"\b(how many|how much|how often|number of|total number|count of|"
    r"list all|all of the|how many times)\b",
    re.I,
)
_PREFERENCE_RE = re.compile(
    r"\b(suggest|recommend|recommendation|what should i|which .* should i|"
    r"advise|propose|ideas for|options for)\b",
    re.I,
)
# "how often / times a week" is a FREQUENCY question (the answer is the current
# rate), not an aggregation/count — and "how often" is literally in _AGGREGATION_RE
# above, so it must be suppressed before the aggregation branch fires.
_FREQUENCY_RE = re.compile(
    r"\b(how often|how regularly|how frequently|"
    r"how many times (?:a|per)\b|times (?:a|per) (?:day|week|month|year))",
    re.I,
)


def classify_answer_mode(
    question: str, question_type: str | None = None,
) -> AnswerMode:
    """Pick an answer mode from the question (and an oracle type when supplied).

    The eval passes the LongMemEval ``question_type`` label for an oracle route;
    production passes ``None`` and we classify heuristically from the question.
    Temporal is matched before aggregation on purpose ("how many days" → temporal).
    """
    if question_type and question_type in _TYPE_TO_MODE:
        return _TYPE_TO_MODE[question_type]
    if _TEMPORAL_RE.search(question):
        return AnswerMode.TEMPORAL
    if _AGGREGATION_RE.search(question) and not _FREQUENCY_RE.search(question):
        return AnswerMode.AGGREGATION
    if _PREFERENCE_RE.search(question):
        return AnswerMode.PREFERENCE
    return AnswerMode.EXTRACTIVE


# Grounding + recency guard applied in EVERY mode. The recency clause is what
# carries the knowledge-update capability (prefer the most recent on conflict)
# regardless of which analytical mode is selected.
_GROUNDING = (
    "Answer using only the supplied memories. Each memory may be tagged with the "
    'date it was recorded, e.g. "[recorded 2023/05/15]" — treat these dates as '
    "ground truth about when each fact was stated, and when two memories conflict "
    "about the same thing, trust the most recently recorded one. Do not invent "
    "facts."
)

# Analytical modes use a two-step Chain-of-Note + JSON reading protocol: extract
# the relevant facts as structured notes FIRST, then derive the answer from the
# notes. The LongMemEval paper found CoN-then-JSON reading worth ~+10 absolute
# points (gpt-4o), and it is the common thread across the >0.80 systems. The
# extractive mode stays concise (single-hop fact lookup is already strong; CoN
# there only adds cost/noise risk).
_MODE_INSTRUCTIONS: dict[AnswerMode, str] = {
    AnswerMode.EXTRACTIVE: (
        "- First locate the exact span in the memories that answers the "
        "question.\n"
        "- If such a span exists you MUST answer — never say 'I don't know' "
        "when a supporting span exists. Respond with just the fact/value the "
        "question asks for: no preamble, no restatement of the question, no "
        "sentence wrapper.\n"
        "- Only if NO span in the memories answers the question, respond "
        "exactly: I don't know."
    ),
    AnswerMode.TEMPORAL: (
        "This is a TEMPORAL question — the answer is derived from dates. Work in "
        "two steps.\n"
        "Step 1 — Notes: for each memory mentioning an event relevant to the "
        "question, emit one JSON note "
        '{\"memory\": <n>, \"date\": \"<ISO YYYY-MM-DD>\", \"event\": \"<what happened>\"}. '
        "Skip irrelevant memories.\n"
        "Step 2 — Compute: from your notes, take the exact dates of the events the "
        "question asks about and compute the result CAREFULLY (count the days/"
        "weeks/months between them, or decide which came first). Show the "
        "subtraction explicitly.\n"
        "Then end with one line: 'Answer: <result>'.\n"
        "If the relevant events are present, DERIVE the answer — only respond "
        "'Answer: I don't know.' if the events themselves are absent."
    ),
    AnswerMode.AGGREGATION: (
        "This is an AGGREGATION question — counting or combining items that may "
        "span multiple sessions. Work in two steps.\n"
        "Step 1 — Notes: scan EVERY memory and emit one JSON note per distinct "
        'item the question asks about {\"memory\": <n>, \"item\": \"<the item>\"}. '
        "Be exhaustive across all sessions.\n"
        "Step 2 — Combine: deduplicate notes that refer to the same item, then "
        "count or combine them.\n"
        "Then end with one line: 'Answer: <count or combined result>'.\n"
        "If items are present, count them — do not abstain because no single "
        "memory states the total. Only respond 'Answer: I don't know.' if no "
        "relevant items are present."
    ),
    AnswerMode.PREFERENCE: (
        "This is a PREFERENCE-APPLICATION question — the memories hold the user's "
        "stated preferences. Work in two steps.\n"
        "Step 1 — Notes: for each stated preference relevant to the request, emit "
        'a JSON note {\"memory\": <n>, \"date\": \"<ISO YYYY-MM-DD>\", '
        '\"preference\": \"<the exact preference>\"}.\n'
        "Step 2 — Apply: using the MOST RECENT relevant preference, answer by "
        "applying it — state what the user would prefer, or recommend options "
        "consistent with it. Briefly name the stated preference you are "
        "applying (e.g. 'Since you prefer X, ...') so the application is "
        "visible, not implicit.\n"
        "Then end with one line: 'Answer: <the applied preference / recommendation>'.\n"
        "Do not refuse because the memories contain no ready-made answer — the "
        "expected answer is the preference applied to the request."
    ),
}

_SYSTEM = (
    "You are a precise assistant that answers questions grounded in the user's "
    "supplied memories. For analytical questions you first extract the relevant "
    "facts as structured notes, then derive the answer from those notes, "
    "reasoning step by step."
)


def build_answer_messages(
    question: str, memory_block: str, mode: AnswerMode,
) -> list[dict[str, str]]:
    """Build the ``[system, user]`` chat messages for the given answer mode."""
    user = (
        f"{_GROUNDING}\n\n"
        f"Memories (most relevant first):\n{memory_block}\n\n"
        f"Question: {question}\n\n"
        f"Rules:\n{_MODE_INSTRUCTIONS[mode]}"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def suggested_max_tokens(mode: AnswerMode, extractive_default: int = 256) -> int:
    """Analytical modes need room for the JSON notes + the derivation + answer."""
    if mode is not AnswerMode.EXTRACTIVE:
        return max(extractive_default, 768)
    return extractive_default


_DATE_TAG_RE = re.compile(r"\[recorded\s+(\d{4}[-/]\d{1,2}[-/]\d{1,2})")


def chronological_sort(documents: list[str]) -> list[str]:
    """Stable-sort date-tagged documents ascending by recorded date.

    Chronological ordering helps the reader reason about event order and apply
    the most-recent fact on conflict (paper: chronological sort + recency).
    Documents are tagged ``[recorded YYYY/MM/DD …]`` at ingest; undated docs keep
    their original order at the end (stable). No-op shape for dateless inputs.
    """
    def sort_key(item: tuple[int, str]) -> tuple[int, str, int]:
        i, doc = item
        m = _DATE_TAG_RE.search(doc)
        if m:
            return (0, m.group(1).replace("/", "-"), i)
        return (1, "", i)

    return [doc for _, doc in sorted(enumerate(documents), key=sort_key)]


def extract_final_answer(text: str) -> str:
    """Return the concise final answer from a possibly step-by-step response.

    The temporal/aggregation modes ask the reader to end with an
    ``Answer: <result>`` line. When present, return what follows the last such
    marker (the derived result); otherwise return the text unchanged. Keeps the
    downstream judge / citation binder focused on the result, not the scratch
    work, without discarding answers from the extractive mode (which has no
    marker).
    """
    matches = list(re.finditer(r"(?im)^\s*answer\s*[:\-]\s*(.+)$", text))
    if matches:
        return matches[-1].group(1).strip()
    return text.strip()
