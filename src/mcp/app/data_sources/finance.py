# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Open Exchange Rates -- free currency data, no API key required."""
from __future__ import annotations

import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_CURRENCY_RE = re.compile(
    r"\b(USD|EUR|GBP|JPY|CHF|CAD|AUD|NZD|CNY|INR|BRL|MXN|KRW|SEK|NOK|DKK|"
    r"dollar|euro|pound|yen|franc|currency|exchange rate|convert)\b", re.I,
)


class ExchangeRatesSource(DataSource):
    name = "exchange_rates"
    description = "Open Exchange Rates -- free currency data. No API key required."
    requires_api_key = False
    domains: list[str] = ["finance"]

    def is_relevant(self, raw_query: str, keywords: list[str]) -> bool:
        """Only relevant for currency/exchange-rate queries."""
        return bool(_CURRENCY_RE.search(raw_query))

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        if not _CURRENCY_RE.search(query):
            return []  # Only respond to currency-related queries
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://open.er-api.com/v6/latest/USD")
                resp.raise_for_status()
                data = resp.json()
                rates = data.get("rates", {})
                top = {k: rates[k] for k in ["EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR"] if k in rates}
                content = "Current exchange rates (base: USD):\n"
                content += "\n".join(f"  {k}: {v:.4f}" for k, v in top.items())
                content += f"\nLast updated: {data.get('time_last_update_utc', 'unknown')}"
                return [DataSourceResult(
                    title="Currency Exchange Rates (USD base)",
                    content=content,
                    source_url="https://open.er-api.com",
                    source_name="Open Exchange Rates",
                    confidence=0.9,
                )]
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Exchange rates query failed: %s", exc)
            return []
