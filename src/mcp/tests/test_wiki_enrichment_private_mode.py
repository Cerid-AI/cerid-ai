# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F091 — Private Mode must close the wiki-enrichment egress channel.

``enrich()`` ships entity names lifted from the user's own KB to six
third-party services. Private Mode L2 ("skip KB") and above is enforced
server-side everywhere else precisely because a direct API/MCP/background
caller has no client-side gate; the background wiki-refresh job had none at
all, so the only path in the service layer that sends user-derived strings
off-box was also the only one Private Mode did not reach.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.external_apis.wiki_enrichment import enrich

pytestmark = pytest.mark.asyncio


def _all_enabled_registry() -> Any:
    mod = MagicMock()
    mod.is_enabled.return_value = True
    return mod


@pytest.mark.parametrize("level", [2, 3, 4])
async def test_private_mode_blocks_outbound_enrichment(level: int) -> None:
    with (
        patch("app.services.private_mode.get_private_mode_level", return_value=level),
        patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
        patch("app.services.external_apis.wiki_enrichment.WikidataAdapter") as MockData,
    ):
        instance = MockWiki.return_value
        instance.lookup = AsyncMock(return_value={"title": "Elon Musk", "extract": "x"})
        MockData.return_value.lookup = AsyncMock(return_value={})

        refs = await enrich("Elon Musk", "person", registry=_all_enabled_registry())

    assert refs == []
    instance.lookup.assert_not_awaited()


@pytest.mark.parametrize("level", [0, 1])
async def test_below_skip_kb_level_enrichment_still_runs(level: int) -> None:
    with (
        patch("app.services.private_mode.get_private_mode_level", return_value=level),
        patch("app.services.external_apis.wiki_enrichment.WikipediaAdapter") as MockWiki,
        patch("app.services.external_apis.wiki_enrichment.WikidataAdapter"),
    ):
        instance = MockWiki.return_value
        instance.lookup = AsyncMock(return_value={
            "title": "Elon Musk",
            "extract": "Business magnate.",
            "content_url": "https://en.wikipedia.org/wiki/Elon_Musk",
        })

        refs = await enrich("Elon Musk", "person", registry=_all_enabled_registry())

    instance.lookup.assert_awaited()
    assert [r.source for r in refs] == ["wikipedia"]
