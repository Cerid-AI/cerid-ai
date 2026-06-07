# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical parser for an artifact's ``tags`` field.

``app.db.neo4j.artifacts.list_artifacts`` / ``get_artifact`` return ``tags``
as the *raw JSON string* stored in Neo4j (canonically ``"[]"`` or a serialized
object) — NOT a parsed structure. Readers that want object-keyed metadata
(daily digest, digests router) must parse it first; doing ``tags.get(...)`` on
the raw string raises ``AttributeError`` (the digests 500 / daily-digest
inbox bug class). This helper is the one place that coercion lives.
"""
from __future__ import annotations

import json
from typing import Any


def parse_tag_object(raw: Any) -> dict[str, Any]:
    """Coerce an artifact ``tags`` value to a dict of keyed metadata.

    Handles the three real shapes: an already-parsed dict (returned as-is), a
    JSON string (parsed; dicts kept, lists/scalars treated as "no metadata"),
    and None/empty. Never raises — a non-dict shape yields ``{}``.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
