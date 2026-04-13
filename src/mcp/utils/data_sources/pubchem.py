# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PubChem data source -- free chemical compound data, no API key required."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger


class PubChemSource(DataSource):
    name = "pubchem"
    description = "PubChem -- chemical compound data and descriptions. No API key required."
    requires_api_key = False
    domains: list[str] = ["research", "chemistry"]

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Search for compound by name
                encoded_query = quote(query, safe="")
                search_resp = await client.get(
                    f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_query}/description/JSON",
                )
                if search_resp.status_code != 200:
                    return []

                data = search_resp.json()
                descriptions = data.get("InformationList", {}).get("Information", [])

                results: list[DataSourceResult] = []
                for info in descriptions[:2]:
                    cid = info.get("CID", "")
                    desc = info.get("Description", "")
                    source = info.get("DescriptionSourceName", "PubChem")
                    if desc:
                        results.append(DataSourceResult(
                            title=f"{query} (CID: {cid})" if cid else query,
                            content=desc,
                            source_url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else "",
                            source_name=source,
                            confidence=0.80,
                        ))

                return results
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("PubChem query failed: %s", exc)
            return []
