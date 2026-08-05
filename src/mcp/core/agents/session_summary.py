# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Once-per-session summarization — the Graphiti episode -> semantic layer.

Bi-temporal memory plan Phase E (E1). Production extraction is per-response
(``core/agents/memory.py``) with no session-level consolidation; this module
adds the missing layer: it folds a whole conversation's turns into a single
semantic summary (key facts, decisions, preferences, open threads) that sits
*above* the raw per-response memories — the multi-session recall lever.

Cost + SLO model: **exactly one LLM call per session**, budget-bounded by the
env-tunable ``SESSION_SUMMARY_LLM_BUDGET_S`` (generous by default). This runs
in a BACKGROUND processor job (``app/processor/jobs/session_summary.py``), NOT
the live-chat ``/sdk`` request path, so the interactive 10s ``/sdk`` SLO and the
per-response ``MEMORY_LLM_BUDGET_S`` (``memory.py``) are structurally untouched
whether this is on or off — the budget here only bounds a stalled background
generation, never an interactive request.

Store-free + DI-friendly (like ``core.agents.entity_extraction``): the LLM
caller is injected so tests drive it with a stub, and the module holds no
Chroma / Neo4j handles. The processor job owns fetching turns and persisting
the result.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.session-summary")

# Background budget — NOT the interactive /sdk SLO. Generous default (20s):
# a session summary consolidates many turns and runs off the request path, so
# a single background generation can take longer than the per-response
# extractor without touching any live-chat bound. Env-tunable so operators can
# tighten it if the local model is slow.
SESSION_SUMMARY_LLM_BUDGET_S = float(os.getenv("SESSION_SUMMARY_LLM_BUDGET_S", "20.0"))

# Deterministic input guards. Too little text -> nothing worth consolidating
# (returns None, no LLM call). Cap the input so a pathologically long session
# can't blow the context window or the background budget; the slice keeps the
# most-recent turns (the tail the caller assembles chronologically).
SESSION_SUMMARY_MIN_INPUT_CHARS = int(os.getenv("SESSION_SUMMARY_MIN_INPUT_CHARS", "200"))
SESSION_SUMMARY_MAX_INPUT_CHARS = int(os.getenv("SESSION_SUMMARY_MAX_INPUT_CHARS", "12000"))

# The session summary is a conversations-domain memory; "conversational" is the
# decaying memory type (see calculate_memory_score) — a session recap is not an
# immortal empirical fact.
SESSION_SUMMARY_MEMORY_TYPE = "conversational"

LlmCaller = Callable[[list[dict[str, str]]], Awaitable[str]]


async def default_llm_caller(messages: list[dict[str, str]]) -> str:
    """Production caller: routes through ``call_internal_llm`` with the
    ``session_summary`` stage breadcrumb so the call appears in structlog +
    Sentry scope correctly (CLAUDE.md observability contract)."""
    from core.utils.internal_llm import call_internal_llm

    return await call_internal_llm(
        messages,
        temperature=0.1,
        max_tokens=800,
        response_format={"type": "json_object"},
        stage="session_summary",
    )


def _build_prompt(*, turns_text: str, session_date: str) -> str:
    date_guidance = ""
    if session_date:
        date_guidance = (
            f"This session occurred on {session_date}. When the content "
            "references relative time ('yesterday', 'last week'), resolve it to "
            f"an ABSOLUTE date relative to {session_date} and state that "
            "absolute date in the summary.\n\n"
        )
    return (
        "Consolidate the following memories from one conversation session into "
        "a SINGLE semantic summary. Capture the durable signal: key facts, "
        "decisions made, stated preferences, and open threads / unresolved "
        "questions. Prefer absolute dates where known. Be concise and "
        "non-redundant — this summary sits above the raw turns, so do not "
        "repeat trivia.\n\n"
        + date_guidance
        + "Return ONLY a JSON object with keys: content, summary, event_date.\n"
        "- content: the consolidated session summary (a few sentences)\n"
        "- summary: a one-line gist (max 500 chars)\n"
        "- event_date: the absolute date the session is about, as ISO "
        "YYYY-MM-DD, or null if no date applies\n\n"
        f"Session memories:\n{turns_text}\n\n"
        "JSON object:"
    )


async def summarize_session(
    *,
    turns_text: str,
    conversation_id: str,
    session_date: str,
    llm_caller: LlmCaller,
) -> dict[str, Any] | None:
    """Consolidate one session's turns into a single semantic summary.

    Parameters
    ----------
    turns_text:
        The session's turns/memories, assembled chronologically by the caller.
    conversation_id:
        Correlation id (logging only — the summary is store-free here).
    session_date:
        The date the session occurred (ISO ``YYYY-MM-DD``); grounds relative
        dates and seeds ``event_date`` when the LLM pins none.
    llm_caller:
        Injected ``(messages) -> str`` coroutine (DI). Production passes
        :func:`default_llm_caller`; tests pass a stub.

    Returns
    -------
    ``{"content", "summary", "memory_type": "conversational", "event_date"}``
    or ``None`` when the input is too short / empty or the single LLM call
    fails, times out, or yields nothing usable. ``None`` is a clean skip, never
    an error — the caller simply does not persist a summary this pass.
    """
    text = (turns_text or "").strip()
    if len(text) < SESSION_SUMMARY_MIN_INPUT_CHARS:
        return None
    text = text[:SESSION_SUMMARY_MAX_INPUT_CHARS]

    prompt = _build_prompt(turns_text=text, session_date=session_date)

    try:
        content = await asyncio.wait_for(
            llm_caller([{"role": "user", "content": prompt}]),
            timeout=SESSION_SUMMARY_LLM_BUDGET_S,
        )
    except asyncio.TimeoutError as exc:
        log_swallowed_error("core.agents.session_summary.timeout", exc)
        logger.warning(
            "Session summary LLM call exceeded %.1fs budget (conversation=%s) — skip",
            SESSION_SUMMARY_LLM_BUDGET_S,
            conversation_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — one LLM call; many failure types, none fatal
        log_swallowed_error("core.agents.session_summary", exc)
        logger.warning("Session summary LLM call failed (conversation=%s): %s", conversation_id, exc)
        return None

    parsed = parse_llm_json(content)
    # The LLM may wrap the object in a single-element array — normalize.
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else None
    if not isinstance(parsed, dict):
        return None

    summary_content = str(parsed.get("content", "")).strip()
    if not summary_content:
        return None

    ev = parsed.get("event_date")
    event_date = str(ev) if ev and str(ev).lower() != "null" else (session_date or "")

    return {
        "content": summary_content[:4000],
        "summary": str(parsed.get("summary", ""))[:500],
        "memory_type": SESSION_SUMMARY_MEMORY_TYPE,
        "event_date": event_date,
    }
