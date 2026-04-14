# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PubChem data source -- free chemical compound data, no API key required."""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_CHEMICAL_RE = re.compile(
    r"\b(?:chemical|compound|molecule|drug|pharmaceutical|medication|"
    r"element|atom|ion|reaction|synthesis|formula|molecular|"
    r"aspirin|ibuprofen|acetaminophen|caffeine|penicillin|insulin|"
    r"methane|ethanol|benzene|glucose|sucrose|protein|amino acid|"
    r"hydrogen|oxygen|nitrogen|carbon|sodium|potassium|chlorine|"
    r"pH|molar|solubility|toxicity|pharmacology)\b",
    re.I,
)
_CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
_IUPAC_RE = re.compile(r"\b\d*-?(?:methyl|ethyl|propyl|butyl|phenyl|amino|hydroxy|oxy|chloro)\b", re.I)


class PubChemSource(DataSource):
    name = "pubchem"
    description = "PubChem -- chemical compound data and descriptions. No API key required."
    requires_api_key = False
    domains: list[str] = ["research", "chemistry"]

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Extract compound names, CAS numbers, or IUPAC patterns.

        PubChem API expects a single compound name, not keyword soup.
        """
        cas = _CAS_RE.search(raw_query)
        if cas:
            return cas.group()
        # Extract the first keyword that triggered chemical detection
        for kw in keywords:
            if _CHEMICAL_RE.search(kw) or _IUPAC_RE.search(kw):
                return kw
        # Fallback: use only the first keyword (most likely the compound name)
        return keywords[0] if keywords else raw_query

    def is_relevant(self, raw_query: str, keywords: list[str]) -> bool:
        """Only relevant for chemistry/pharmaceutical queries."""
        return bool(
            _CHEMICAL_RE.search(raw_query)
            or _CAS_RE.search(raw_query)
            or _IUPAC_RE.search(raw_query)
        )

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
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("PubChem query failed: %s", exc)
            return []
