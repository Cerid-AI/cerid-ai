# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Utilities for parsing LLM response content."""

from __future__ import annotations

import json
import logging
from typing import Any

_logger = logging.getLogger("ai-companion.llm_parsing")


def parse_llm_json(content: str) -> Any:
    """Strip markdown code fences and parse JSON from an LLM response.

    Handles: ```json ... ```, ``` ... ```, and plain JSON.

    On strict-parse failure, attempts a best-effort recovery for the
    common case of truncated output (gemini-2.5-flash hitting max_tokens
    mid-string). The recovery walks the text, tracks bracket and string
    state, drops the trailing partial token, and closes any open
    brackets — returning the largest valid prefix. Re-raises the
    original JSONDecodeError if recovery isn't feasible.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        repaired = _truncate_and_close(text)
        if repaired is None:
            raise
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError:
            raise err from None
        _logger.info("Recovered truncated JSON (orig=%d bytes, repaired=%d bytes)", len(text), len(repaired))
        return value


def _truncate_and_close(text: str) -> str | None:
    """Best-effort: trim trailing partial token and close open brackets.

    Returns the recovered text, or None if recovery isn't feasible
    (unbalanced brackets, no open brackets, or empty input).
    """
    stack: list[str] = []
    in_string = False
    escape = False
    last_safe = 0

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
                last_safe = i + 1
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "[{":
            stack.append("]" if ch == "[" else "}")
            last_safe = i + 1
        elif ch in "]}":
            if not stack or stack[-1] != ch:
                return None
            stack.pop()
            last_safe = i + 1
        elif ch == ",":
            last_safe = i + 1
        elif not ch.isspace():
            last_safe = i + 1

    if not stack:
        return None

    fragment = text[:last_safe].rstrip()
    while fragment.endswith(","):
        fragment = fragment[:-1].rstrip()
    return fragment + "".join(reversed(stack))
