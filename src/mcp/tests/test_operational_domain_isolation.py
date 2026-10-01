# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A consumer's operational domain stays out of the owner's retrieval.

``anneal_*``, ``boardroom_*`` and ``trading`` are kept out of the taxonomy so a
personal query never reaches an orchestrator's or a product's corpus. Each one
still gets a ``:Domain`` node on first ingest, and boot rehydration merges every
such node into ``config.DOMAINS``. On the live stack that put all of them into
the owner's default search and into the adjacent-domain bleed: a query scoped to
``tasks`` returned rows from ``anneal_decisions``.

These tests put the domain into ``config.DOMAINS`` the way boot does, through
``merge_persisted_domains``, and assert on the collections the real
``multi_domain_query`` opens.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import config
import config.settings
from app.startup.domain_rehydration import merge_persisted_domains
from utils.domain_privacy import operational_domains, owner_domains

OPERATIONAL = "anneal_isolation_probe"
OPERATOR_MADE = "operator_made_probe"


class _RecordingChromaClient:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name(d)) for d in config.DOMAINS]

    def get_collection(self, name: str) -> Any:
        self.opened.append(name)
        return SimpleNamespace(
            query=lambda **kw: {
                "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]],
            },
        )


@pytest.fixture
def _booted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The state after boot: one consumer owns OPERATIONAL, and rehydration has
    merged it and an operator-made domain into config.DOMAINS."""
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    monkeypatch.setattr(config, "TAXONOMY", dict(config.TAXONOMY))
    monkeypatch.setattr(config, "DOMAINS", list(config.DOMAINS))
    monkeypatch.setitem(
        config.settings.CONSUMER_REGISTRY,
        "isolation-probe",
        {"rate_limits": {}, "allowed_domains": [OPERATIONAL, "finance"], "strict_domains": True},
    )
    added = merge_persisted_domains([
        {"name": OPERATIONAL, "description": "", "icon": "", "sub_categories": []},
        {"name": OPERATOR_MADE, "description": "", "icon": "", "sub_categories": []},
    ])
    assert added == 2


@pytest.mark.usefixtures("_booted")
def test_rehydration_still_registers_the_domain_for_admin_and_sync() -> None:
    assert OPERATIONAL in config.DOMAINS


@pytest.mark.usefixtures("_booted")
def test_only_a_consumer_owned_domain_outside_the_taxonomy_is_operational() -> None:
    hidden = operational_domains()
    assert OPERATIONAL in hidden
    # A taxonomy domain a consumer is also granted stays the owner's.
    assert "finance" not in hidden
    assert OPERATOR_MADE not in hidden


@pytest.mark.usefixtures("_booted")
def test_owner_domains_keeps_everything_but_the_operational_ones() -> None:
    owned = owner_domains()
    assert OPERATIONAL not in owned
    assert OPERATOR_MADE in owned
    assert "finance" in owned


@pytest.mark.usefixtures("_booted")
def test_adjacent_domain_bleed_never_names_an_operational_domain() -> None:
    from core.agents.query_agent import _get_adjacent_domains

    adjacent = _get_adjacent_domains(["finance"])
    assert OPERATIONAL not in adjacent
    assert OPERATOR_MADE in adjacent


@pytest.mark.asyncio
@pytest.mark.usefixtures("_booted")
async def test_all_domains_scan_does_not_open_an_operational_collection() -> None:
    from core.agents import query_agent

    client = _RecordingChromaClient()
    await query_agent.multi_domain_query(
        query="anything", domains=None, top_k=3, chroma_client=client,
    )

    assert config.collection_name(OPERATIONAL) not in client.opened
    assert config.collection_name("general") in client.opened
    assert config.collection_name(OPERATOR_MADE) in client.opened


@pytest.mark.asyncio
@pytest.mark.usefixtures("_booted")
async def test_a_caller_that_names_the_operational_domain_reaches_it() -> None:
    from core.agents import query_agent

    client = _RecordingChromaClient()
    await query_agent.multi_domain_query(
        query="anything", domains=[OPERATIONAL], top_k=3, chroma_client=client,
    )

    assert client.opened == [config.collection_name(OPERATIONAL)]


# Modules that turn "no domains named" into a list of collections to search.
_RETRIEVAL_MODULES = (
    "core/agents/query_agent.py",
    "core/agents/self_rag.py",
    "core/agents/hallucination/verification.py",
    "core/agents/hallucination/streaming.py",
)
# The one reader left: multi_domain_query's warning about a domain it does not
# know, which has to compare against every registered domain.
_ALLOWED = {"core/agents/query_agent.py": 2}


def test_retrieval_modules_do_not_read_all_domains_from_config() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in _RETRIEVAL_MODULES:
        code = "\n".join(
            line.split("#", 1)[0] for line in (root / rel).read_text().splitlines()
        )
        found = len(re.findall(r"\bconfig\.DOMAINS\b", code))
        assert found == _ALLOWED.get(rel, 0), (
            f"{rel} reads config.DOMAINS {found} time(s); a search over every "
            "domain must start from utils.domain_privacy.owner_domains()"
        )
