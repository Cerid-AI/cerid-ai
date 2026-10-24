# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""What a query may send to external sources (ruled 2026-10-10).

A general question goes out as asked, and "latest" or "current" in it means a
stale KB answer should be checked on the web. A personal question stays local:
those words never send it out, and when the knowledge base is too weak to
answer it, it goes out only as a query for a related public fact, rewritten by
a local model with every private datapoint removed. If no public fact would
help, nothing goes out.

A question is personal when it speaks of the user ("my", "our", "I" — but not
"tell me" or "can you", which address the assistant) or carries a private
datapoint on its face: an email address, an amount, an account- or
phone-shaped number. A name is not private (ruled 2026-10-11): "what did Sarah
say about the merger" is a general question and goes out as asked. Private
means PII: contact details, amounts, account, card and ID numbers, and the
dates of the user's own events.
"""
from __future__ import annotations

import re

import config
from core.utils.swallowed import log_swallowed_error
from core.utils.temporal import parse_temporal_intent

GENERALIZE_STAGE = "external_query_generalize"

_FIRST_PERSON = re.compile(
    r"\b(?:i|i'm|i've|i'd|i'll|me|my|mine|myself|we|we're|we've|we'd|us|our|ours|ourselves)\b",
    re.IGNORECASE,
)
# A claim about the user's own data reads in the second person ("your balance
# is $12,400") as often as the first.
_SECOND_PERSON = re.compile(r"\b(?:you|you're|you've|your|yours|yourself)\b", re.IGNORECASE)
_FRESHNESS = re.compile(
    r"\b(?:latest|current(?:ly)?|newest|most\s+recent|up[- ]to[- ]date|right\s+now|these\s+days)\b",
    re.IGNORECASE,
)
# Phrases that address the assistant, not the user's own data.
_ASSISTANT_ADDRESS = re.compile(r"\b(?:(?:tell|show|give|help|remind|let)\s+me|can\s+you|could\s+you)\b", re.IGNORECASE)
# A private datapoint: an email address (also spelled out), a link, an amount
# ("$12,400", "12,400 USD"; a bare "120k" is ambiguous and not caught), a date
# ("01/02/1990"), a long digit run, or an account- or phone-shaped number.
# Short versions and years ("3.12", "2024") are not. It errs toward private: a
# decimal such as "180.16" also matches, which costs only a local rewrite (the
# public fact still goes out, the number stripped), whereas a looser pattern
# lets dotted account numbers and dates through. One pattern both marks a
# question personal and is stripped from a rewrite, so nothing the detector
# would flag can leave in the rewrite.
_PRIVATE_DATAPOINT = re.compile(
    r"\S{1,64}@\S{1,255}|https?://\S{1,2000}"
    r"|[$€£]\s?\d[\d,.]{0,15}"
    r"|\b(?:usd|eur|gbp)\s?\d[\d,.]{0,15}"
    r"|\b\d[\d,]{0,15}(?:\.\d{1,4})?\s?(?:usd|dollars?|eur|euros?|gbp|pounds?)\b"
    r"|\b\w{1,64}\s{1,3}(?:at|@)\s{1,3}\w{1,64}\s{1,3}dot\s{1,3}\w{1,24}\b"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    r"|\d{5,}"
    r"|\b\d{3,6}(?:[-\s.]\d{2,6}){1,4}\b",
    re.IGNORECASE,
)
_MAX_WORDS = 12

_GENERALIZE_PROMPT = (
    "The user asked a question about their own data. Write a short web search query "
    "for a PUBLIC fact that would help answer it accurately, such as a current price, "
    "rate, rule, release or definition. The query must contain no personal identifiers: "
    "no contact details (email, phone, address), amounts or balances, account, card, "
    "reference or ID numbers, or dates of their own events. Names may stay when they help. "
    "If no public fact would help, reply NONE. "
    "Reply with the query alone.\n"
    "Examples:\n"
    "what is my VTSAX balance worth today -> VTSAX share price\n"
    "how much tax do I owe on my salary this year -> federal income tax brackets\n"
    "what did we decide about the garden project -> NONE"
)


_GENERALIZE_CLAIM_PROMPT = (
    "The statement is about the user's own data. Write a short web search query for a "
    "PUBLIC fact that would help check it, such as a current price, rate, rule, release or "
    "definition. The query must contain no personal identifiers: no contact details "
    "(email, phone, address), amounts or balances, account, card, reference or ID numbers, "
    "or dates of their own events. Names may stay when they help. If no public fact would "
    "help, reply NONE. Reply with the query alone."
)


def is_personal_query(query: str) -> bool:
    text = query or ""
    if _PRIVATE_DATAPOINT.search(text):
        return True
    return bool(_FIRST_PERSON.search(_ASSISTANT_ADDRESS.sub(" ", text)))


def freshness_intent(query: str) -> int | None:
    """Days a KB answer stays fresh enough for *query*, or None when freshness is not asked about.

    A time phrase ("this week", "recently") holds any general question to its
    window, hard or soft alike: freshness here judges the KB answer and drops no
    rows. "latest" or "current" hold it to the staleness floor. A personal
    question has none: how fresh the user's own data is, the web cannot say.
    """
    if is_personal_query(query):
        return None
    intent = parse_temporal_intent(query)
    if intent is not None:
        return intent.days
    if _FRESHNESS.search(query or ""):
        return int(getattr(config, "CRAG_STALENESS_WINDOW_DAYS", 7))
    return None


def _clean(text: str) -> str:
    lines = (text or "").strip().splitlines()
    line = lines[0].strip().strip("\"'`") if lines else ""
    if not line or line.upper().rstrip(".").startswith("NONE"):
        return ""
    line = " ".join(_FIRST_PERSON.sub(" ", _PRIVATE_DATAPOINT.sub(" ", line)).split()[:_MAX_WORDS])
    # The same test that sends a general question out as asked: a rewrite that
    # still reads as personal does not leave.
    return "" if is_personal_query(line) else line


async def _public_fact_query(text: str, prompt: str) -> str | None:
    try:
        from core.utils.internal_llm import call_internal_llm

        rewrite = await call_internal_llm(
            [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
            stage=GENERALIZE_STAGE,
            temperature=0.0,
            max_tokens=40,
            interactive=True,
        )
    except Exception as exc:  # noqa: BLE001 — no local rewrite means nothing goes out
        log_swallowed_error(f"{__name__}.generalize", exc)
        return None
    return _clean(rewrite) or None


async def external_query_for(query: str) -> str | None:
    """The text that may leave the host for *query*: itself, a public-fact rewrite, or None."""
    if not is_personal_query(query):
        return query
    return await _public_fact_query(query, _GENERALIZE_PROMPT)


def is_personal_claim(claim: str) -> bool:
    return is_personal_query(claim) or bool(_SECOND_PERSON.search(claim or ""))


async def external_query_for_claim(claim: str) -> str | None:
    """What a web or data-source check of *claim* may send: the claim, a public-fact query, or None.

    Verification checks an answer's claims against the web. A claim from an
    answer about the user's own data ("your balance is $12,400") follows the
    same rule as a personal question.
    """
    if not is_personal_claim(claim):
        return claim
    return await _public_fact_query(claim, _GENERALIZE_CLAIM_PROMPT)
