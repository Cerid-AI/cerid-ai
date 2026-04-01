# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Context compression — summarize conversation history to fit target token budget.

Strategies:
1. **sliding_window_prune** — pure truncation: keep system message + last N
   user/assistant pairs.  Zero LLM calls, instant.
2. **compress_history** — LLM-summarised: keep system + last N turns verbatim,
   summarize middle turns into a single compressed message.

Both preserve the system message (if present) and the most recent turns so
the model retains immediate conversational context.
"""

from __future__ import annotations

import logging
import os

import httpx

from utils.circuit_breaker import CircuitOpenError
from utils.llm_client import call_llm

logger = logging.getLogger("ai-companion.context_compression")

# Rough chars-per-token for estimation
CHARS_PER_TOKEN = 3.5

# How many user+assistant *pairs* to keep verbatim at the tail.
# Configurable via env so operators can tune per-model without code changes.
KEEP_RECENT_TURNS = int(os.getenv("CERID_KEEP_RECENT_TURNS", "2"))


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return max(1, int(len(text) / CHARS_PER_TOKEN + 0.5))


def _estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a list of messages."""
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


def sliding_window_prune(
    messages: list[dict],
    max_turns: int | None = None,
) -> list[dict]:
    """Pure truncation — keep system message + last *max_turns* user/assistant pairs.

    This is the lightweight, zero-LLM-call fallback used when the compress
    endpoint is unavailable or when latency matters more than fidelity.

    Args:
        messages: Full conversation history (system + user/assistant messages).
        max_turns: Number of user+assistant pairs to keep.  Defaults to
                   ``KEEP_RECENT_TURNS`` (configurable via CERID_KEEP_RECENT_TURNS).

    Returns:
        A new message list containing only the system message (if present)
        and the most recent *max_turns* user+assistant pairs.
    """
    if max_turns is None:
        max_turns = KEEP_RECENT_TURNS
    if not messages:
        return messages

    # Separate system message (if present)
    system_msg = None
    conversation = list(messages)
    if conversation and conversation[0].get("role") == "system":
        system_msg = conversation.pop(0)

    # Keep last N*2 messages (N pairs of user+assistant)
    keep_count = max_turns * 2
    if len(conversation) <= keep_count:
        # Nothing to prune
        result: list[dict] = []
        if system_msg:
            result.append(system_msg)
        result.extend(conversation)
        return result

    recent = conversation[-keep_count:] if keep_count > 0 else []
    result = []
    if system_msg:
        result.append(system_msg)
    result.extend(recent)
    return result


async def compress_history(
    messages: list[dict],
    target_tokens: int,
) -> list[dict]:
    """Compress conversation history to fit within a target token budget.

    Returns a new message list with middle turns summarized.
    If the history already fits, returns the original messages unchanged.
    """
    if not messages:
        return messages

    current_tokens = _estimate_messages_tokens(messages)
    if current_tokens <= target_tokens:
        return messages

    # Separate system message (if present)
    system_msg = None
    conversation = list(messages)
    if conversation and conversation[0].get("role") == "system":
        system_msg = conversation.pop(0)

    if len(conversation) <= KEEP_RECENT_TURNS * 2:
        # Too few turns to compress — return as-is
        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(conversation)
        return result

    # Split: middle (compressible) and recent (preserved)
    recent_count = KEEP_RECENT_TURNS * 2  # pairs of user+assistant
    middle = conversation[:-recent_count]
    recent = conversation[-recent_count:]

    # Summarize middle via LLM
    summary = await _summarize_turns(middle)

    result = []
    if system_msg:
        result.append(system_msg)
    result.append({
        "role": "user",
        "content": f"[Compressed conversation summary]\n{summary}",
    })
    result.extend(recent)
    return result


async def _summarize_turns(turns: list[dict]) -> str:
    """Summarize a list of conversation turns into a concise summary."""
    turns_text = "\n".join(
        f"{t['role'].upper()}: {t.get('content', '')[:500]}" for t in turns
    )

    prompt = (
        "Summarize this conversation excerpt concisely. Preserve key facts, "
        "decisions, and user preferences. Focus on information the assistant "
        "needs to continue the conversation coherently.\n\n"
        f"CONVERSATION:\n{turns_text[:4000]}\n\n"
        "SUMMARY:"
    )

    try:
        return await call_llm(
            [{"role": "user", "content": prompt}],
            breaker_name="bifrost-compress",
            temperature=0.1,
            max_tokens=500,
        )
    except (CircuitOpenError, httpx.HTTPStatusError, KeyError) as e:
        logger.warning("Context compression LLM call failed: %s", e)
        # Fallback: truncate middle turns to their first line
        lines = []
        for t in turns:
            content = t.get("content", "")
            first_line = content.split("\n")[0][:100]
            lines.append(f"{t['role']}: {first_line}")
        return "\n".join(lines)
