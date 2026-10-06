# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""PubChem must be asked for a chemistry question in any KB domain.

Round 5 item 5.9: ``PubChemSource.domains`` named ``research`` and
``chemistry`` — neither is a TAXONOMY domain — so the registry's domain gate
(``get_enabled_sources(domain)``) dropped it before ``is_relevant`` ever saw
the query whenever retrieval passed the KB domain it was searching, which the
chat path always does. A second defect hid behind the first: ``adapt_query``
returned the chemistry *trigger* word (``molecular``) instead of the compound.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.data_sources import registry
from app.data_sources.base import DataSourceRegistry
from app.data_sources.pubchem import PubChemSource

_QUERY = "what is the molecular formula of caffeine"
_KEYWORDS = ["molecular", "formula", "caffeine"]

_DESCRIPTION = {
    "InformationList": {
        "Information": [
            {"CID": 2519, "Title": "Caffeine"},
            {
                "CID": 2519,
                "Description": "Caffeine is a trimethylxanthine in which the three methyl groups "
                "are located at positions 1, 3, and 7.",
                "DescriptionSourceName": "ChEBI",
            },
        ]
    }
}


def test_pubchem_survives_the_kb_domain_gate():
    names = [s.name for s in registry.get_enabled_sources(domain="general")]
    assert "pubchem" in names


def test_pubchem_is_relevant_to_a_chemistry_question():
    assert PubChemSource().is_relevant(_QUERY, _KEYWORDS) is True


def test_adapt_query_picks_the_compound_not_the_trigger_word():
    assert PubChemSource().adapt_query(_QUERY, _KEYWORDS) == "caffeine"


def test_adapt_query_keeps_a_cas_number():
    assert PubChemSource().adapt_query("toxicity of 58-08-2", ["toxicity"]) == "58-08-2"


@pytest.mark.asyncio
async def test_chemistry_query_in_the_general_domain_reaches_pubchem(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_DESCRIPTION)

    real_client = httpx.AsyncClient

    def patched_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)
    reg = DataSourceRegistry()
    reg.register(PubChemSource())

    results = await reg.query_all(
        " ".join(_KEYWORDS), domain="general", raw_query=_QUERY, keywords=_KEYWORDS,
    )

    assert seen and "/compound/name/caffeine/description/JSON" in seen[0]
    assert len(results) == 1
    assert results[0]["source_name"] == "ChEBI"
    assert "trimethylxanthine" in results[0]["content"]
    assert json.loads(json.dumps(results[0]))["source_url"].endswith("/compound/2519")
