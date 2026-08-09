# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Does a generated summary actually say anything about its subject?

The wiki compiler asks an LLM to summarise an entity from retrieved excerpts.
When the excerpts only mention the entity in passing, the model does not fail —
it writes a fluent paragraph *about the absence*:

    "Apple Inc. is not mentioned in the provided excerpts. However, the
     excerpts do discuss Kubernetes API versioning..."

That is stored as the entity's summary and then served as high-priority
grounding context to the answer path, where it actively harms answers: the
reader is handed a paragraph that denies its own subject and describes
something else. Measured on the live corpus these are only 1.1% of summarised
entities (29/2558) but **27% of the most-mentioned thirty** — a passing mention
repeated across many artifacts is exactly what produces a high mention_count
and a contentless excerpt set, so any "most-mentioned first" sample is biased
straight into them.

This module holds the single shared vocabulary for that shape. Two callers need
it and they must not drift apart:

* ``app.processor.jobs.wiki_refresh`` — refuses to *write* such a summary.
* ``tests.eval.compiled_summary_soak_eval`` — refuses to *measure* one, so the
  metric does not depend on a backfill having run.

``app.eval.ragas_metrics`` composes a different predicate (every sentence of an
*answer* reports absence ⇒ the answer abstained) from the same phrase list.
Same vocabulary, different question — one ruler, two measurements.
"""
from __future__ import annotations

import re

# The model is asked to emit this and nothing else when the excerpts do not
# describe the entity. Cheaper and less ambiguous than inferring from prose —
# but not sufficient on its own, because instruction-following on a local 8B
# model is not reliable. Both checks run; either one skips the write.
INSUFFICIENT_SENTINEL = "INSUFFICIENT_EXCERPTS"

# Phrases that assert the *absence* of grounding rather than a fact about the
# world. Shared with the answer-side abstention detector in ragas_metrics.
ABSENCE_PATTERNS: tuple[str, ...] = (
    r"i\s+don'?t\s+know",
    r"i\s+don'?t\s+have\s+any\s+sources",
    r"there\s+(?:is|are)\s+no\s+(?:\w+\s+){0,2}?information",
    r"no\s+information\s+(?:about|on|regarding|is\s+available)",
    r"(?:is|are)\s+not\s+mentioned\s+in\s+the\s+(?:provided|supplied|available)",
    r"(?:memories|memory|context|excerpts?|sources?)(?:\s+\w+){0,2}?\s+"
    r"(?:do|does)\s+not\s+(?:contain|include|mention|provide|record)",
    r"not\s+(?:mentioned|found|present|recorded|described)\s+in\s+the\s+"
    r"(?:provided\s+|supplied\s+)?(?:memories|context|excerpts?)",
    r"(?:is|are)\s+not\s+a\s+named\s+entity",
    r"no\s+relevant\s+information",
)

ABSENCE_RE = re.compile("|".join(ABSENCE_PATTERNS), re.I)

# Only the FIRST substantive sentence is inspected, and the distinction is
# load-bearing. A good summary may still note a limit —
#
#   "Kubernetes is an API-driven orchestration system, described in the corpus
#    through its versioning policy. The excerpts do not contain information
#    about its release cadence."
#
# — which is honest scoping, not emptiness. Scanning the opening N characters
# rejected exactly that summary in test. The shape worth refusing always LEADS
# with the denial and then changes the subject, so "does it open with one?" is
# the question, not "does it contain one?".
_MARKDOWN_NOISE_RE = re.compile(r"^\s*(?:#{1,6}\s+|\*{1,2}|_{1,2})|(?:\*{1,2}|_{1,2})\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MIN_SENTENCE_WORDS = 6  # skips "**Entity: Helm (ORG)**" style label lines


def _first_substantive_sentence(text: str) -> str:
    """The first sentence that is prose rather than a heading or label."""
    for raw_line in text.split("\n"):
        line = _MARKDOWN_NOISE_RE.sub("", raw_line).strip()
        if not line:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            candidate = sentence.strip()
            if len(candidate.split()) >= _MIN_SENTENCE_WORDS:
                return candidate
    return text.strip()


def is_insufficient_summary(summary: str | None) -> bool:
    """True when the summary opens by asserting it has nothing to describe.

    Conservative by construction: a false positive here deletes a real summary
    during backfill and suppresses a real page during refresh.
    """
    text = (summary or "").strip()
    if not text:
        return True
    if INSUFFICIENT_SENTINEL in text:
        return True
    return bool(ABSENCE_RE.search(_first_substantive_sentence(text)))
