# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A claim about the user's own data goes out only as a public-fact query (audit F119).

Verification checks an answer's claims against the web (SearXNG/Tavily) and the
data-source registry. Both sent the claim as written, so "Your 401k balance is
$12,400 in account 4417-0032" could reach a search engine. The same rule as a
personal question now applies (ruled 2026-10-10). The local model, the search
provider and the registry are faked; the verification code is real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.external_query import is_personal_claim

PERSONAL = "Your 401k balance is $12,400 in account 4417-0032."
GENERAL = "The Eiffel Tower is 330 metres tall."


@pytest.mark.parametrize(("claim", "personal"), [
    (PERSONAL, True),
    ("You decided to pay the garden invoices at the end of the month.", True),
    ("I moved the meeting to Thursday.", True),
    (GENERAL, False),
    ("Python 3.12 was released in October 2023.", False),
])
def test_a_claim_is_personal_by_person_or_datapoint(claim, personal):
    assert is_personal_claim(claim) is personal


@pytest.mark.asyncio
@pytest.mark.parametrize(("claim", "rewrite", "sent"), [
    (GENERAL, None, GENERAL),
    (PERSONAL, "401k contribution limit 2026", "401k contribution limit 2026"),
    (PERSONAL, "NONE", None),
])
async def test_the_web_evidence_search_sends_only_what_may_leave(claim, rewrite, sent):
    from core.agents.hallucination import verification

    search = AsyncMock(return_value={"results": [{"url": "https://example.org/a"}]})
    with (
        patch("utils.web_search.has_real_search_provider", return_value=True),
        patch("utils.web_search.search_and_verify", new=search),
        patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value=rewrite)),
    ):
        urls = await verification._independent_search_evidence_urls(claim, None)

    if sent is None:
        search.assert_not_awaited()
        assert urls == []
    else:
        assert search.await_args.args[0] == sent


@pytest.mark.asyncio
@pytest.mark.parametrize(("rewrite", "queried"), [("401k contribution limit 2026", True), ("NONE", False)])
async def test_the_authoritative_check_sends_only_the_rewrite(rewrite, queried):
    from core.agents.hallucination.authoritative_verify import verify_claim_authoritatively

    registry = AsyncMock()
    registry.query_all = AsyncMock(return_value=[])
    with patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value=rewrite)):
        await verify_claim_authoritatively(PERSONAL, registry=registry)

    if queried:
        assert registry.query_all.await_args.kwargs["raw_query"] == "401k contribution limit 2026"
        assert "4417" not in registry.query_all.await_args.args[0]
    else:
        registry.query_all.assert_not_awaited()
