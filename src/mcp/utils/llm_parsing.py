# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Utilities for parsing LLM response content."""

from __future__ import annotations

import json
from typing import Any


def parse_llm_json(content: str) -> Any:
    """Strip markdown code fences and parse JSON from an LLM response.

    Handles: ```json ... ```, ``` ... ```, and plain JSON.
    """
    text = content.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    return json.loads(text)
