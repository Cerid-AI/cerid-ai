# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wolfram Alpha Short Answers API -- requires WOLFRAM_APP_ID."""
from __future__ import annotations

import os

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger


class WolframAlphaSource(DataSource):
    name = "wolfram_alpha"
    description = "Wolfram Alpha -- computational knowledge. Requires WOLFRAM_APP_ID."
    requires_api_key = True
    api_key_env_var = "WOLFRAM_APP_ID"  # pragma: allowlist secret
    domains: list[str] = []  # all domains

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        app_id = os.getenv("WOLFRAM_APP_ID")
        if not app_id:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "http://api.wolframalpha.com/v1/result",
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
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Wolfram Alpha query failed: %s", exc)
            return []
