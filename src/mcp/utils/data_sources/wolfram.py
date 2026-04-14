# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wolfram Alpha Short Answers API -- requires WOLFRAM_APP_ID."""
from __future__ import annotations

import os
import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_MATH_RE = re.compile(
    r"\b(?:calculate|compute|solve|integrate|derive|differentiate|convert|"
    r"evaluate|simplify|factor|expand|sum of|product of|limit of|"
    r"how (?:many|much)|what is \d)\b",
    re.I,
)
_HAS_MATH_OPS = re.compile(r"[\d].*[+\-*/^=]|[+\-*/^=].*[\d]")
_UNIT_RE = re.compile(
    r"\b(?:meters?|feet|miles?|km|kg|pounds?|lbs?|celsius|fahrenheit|"
    r"liters?|gallons?|inches?|cm|mm|hours?|minutes?|seconds?|bytes?|"
    r"MB|GB|TB|watts?|volts?|amps?|joules?|calories?)\b",
    re.I,
)


class WolframAlphaSource(DataSource):
    name = "wolfram_alpha"
    description = "Wolfram Alpha -- computational knowledge. Requires WOLFRAM_APP_ID."
    requires_api_key = True
    api_key_env_var = "WOLFRAM_APP_ID"  # pragma: allowlist secret
    domains: list[str] = []  # all domains

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Wolfram expects natural-language math/science — pass raw query through."""
        return raw_query

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Reduce confidence for non-answers or overly short responses."""
        content = result.content.strip().lower()
        non_answers = ("no short answer", "wolfram|alpha did not understand", "no result")
        if any(na in content for na in non_answers):
            return 0.3
        # Very short answers (e.g., just a number) are highly reliable
        if len(content) < 50:
            return min(1.0, result.confidence + 0.03)
        return result.confidence

    def is_relevant(self, raw_query: str, keywords: list[str]) -> bool:
        """Only relevant for computational, mathematical, or unit queries."""
        return bool(
            _MATH_RE.search(raw_query)
            or _HAS_MATH_OPS.search(raw_query)
            or _UNIT_RE.search(raw_query)
        )

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        app_id = os.getenv("WOLFRAM_APP_ID")
        if not app_id:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.wolframalpha.com/v1/result",
                    params={"appid": app_id, "i": query},
                )
                if resp.status_code == 200 and resp.text:
                    return [DataSourceResult(
                        title=f"Wolfram Alpha: {query}",
                        content=resp.text,
                        source_url=f"https://www.wolframalpha.com/input?i={query}",
                        source_name="Wolfram Alpha",
                        confidence=0.95,
                    )]
                return []
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Wolfram Alpha query failed: %s", exc)
            return []
