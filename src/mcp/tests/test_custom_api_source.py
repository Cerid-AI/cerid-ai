# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CH1 — CustomApiSource.adapt_query must preserve the full in-context query.

User-configured external APIs should receive the full natural-language
query, not the keyword bag the base DataSource.adapt_query produces (the
keyword-only form drops context that user custom APIs may rely on).
"""

from __future__ import annotations

from app.data_sources.custom import CustomApiSource


def _make_source() -> CustomApiSource:
    return CustomApiSource(
        source_id="acme",
        display_name="Acme Search",
        base_url="https://api.example.com",
    )


def test_adapt_query_returns_full_raw_query():
    source = _make_source()
    result = source.adapt_query(
        "what happened last quarter", ["happened", "quarter"]
    )
    assert result == "what happened last quarter"


def test_adapt_query_ignores_keywords_even_when_present():
    source = _make_source()
    # Base class would join keywords; the override must NOT.
    assert source.adapt_query("raw question", ["a", "b", "c"]) == "raw question"
