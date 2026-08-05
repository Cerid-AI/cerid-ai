# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Meeting summary + action-item extraction via the internal LLM router.

Calls flow through ``utils.internal_llm.call_internal_llm`` with the
``meeting_summary`` stage tag, so operators can route per-stage via
``PROVIDER_STAGE_MEETING_SUMMARY=openrouter`` (etc.) without touching code.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ai-companion.plugins.meeting_capture.summary")

_SYSTEM_PROMPT = """You are a meeting summarizer.

Given a speaker-tagged transcript of a meeting, produce:

  1. A 3-5 sentence summary capturing the key topics and outcomes.
  2. A list of action items as "{owner}: {action}" pairs, where owner is
     the speaker who committed to the action.
  3. A list of decisions made in the meeting.

Output strictly as JSON with this shape:
  {
    "summary": "...",
    "action_items": ["...", "..."],
    "decisions": ["...", "..."]
  }

If the transcript is too short or empty, return all fields as empty strings/arrays."""


def _format_transcript(segments: list[dict[str, Any]]) -> str:
    """Render speaker-tagged segments as a transcript the LLM can read."""
    lines: list[str] = []
    for seg in segments:
        speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


async def summarize_meeting(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate summary + action items + decisions for a meeting.

    Returns:
        summary:      str
        action_items: list[str]
        decisions:    list[str]
    """
    transcript = _format_transcript(segments)
    if not transcript:
        return {"summary": "", "action_items": [], "decisions": []}

    # Lazy import to avoid heavy router imports at plugin load.
    from core.utils.internal_llm import call_internal_llm

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    try:
        raw = await call_internal_llm(
            messages=messages,
            stage="meeting_summary",
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
    except (ValueError, OSError, RuntimeError) as exc:
        logger.warning("Meeting summary LLM call failed: %s", exc)
        return {"summary": "", "action_items": [], "decisions": []}

    # Defensive JSON parse — some providers wrap with ```json fences
    text = raw if isinstance(raw, str) else getattr(raw, "text", "")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Meeting summary LLM returned non-JSON: %s", exc)
        return {"summary": text[:500], "action_items": [], "decisions": []}

    return {
        "summary": data.get("summary", ""),
        "action_items": list(data.get("action_items", [])),
        "decisions": list(data.get("decisions", [])),
    }
