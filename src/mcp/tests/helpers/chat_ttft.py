# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Which SSE line is a generated token.

The chat proxy writes a ``cerid_meta`` frame before any model text. A
measurement that stops on the first ``data:`` line times that frame.
"""

from __future__ import annotations

import json


def sse_data_is_generated_token(line: str) -> bool:
    """True when ``line`` carries model text.

    Routing frames (``cerid_meta``, ``cerid_meta_update``), ``[DONE]``,
    error objects, and a role-only delta are not tokens.
    """
    if not line.startswith("data:"):
        return False
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return False
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict):
        return False
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    first = choices[0]
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = delta.get("content")
    if content is None:
        content = message.get("content")
    return isinstance(content, str) and content != ""
