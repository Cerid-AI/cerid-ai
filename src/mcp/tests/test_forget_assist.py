# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The forget assistant: deterministic candidates, local-only grouping, cloud only with consent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.forget import assist
from core.forget.registry import Entry, Subject
from tests.helpers.forget import isolate_forget

A, B = "a" * 64, "b" * 64


def _cands(n: int) -> list[dict[str, Any]]:
    return [
        {"kind": "artifact", "id": f"x{i}", "store": "knowledge_base", "label": f"doc{i}", "excerpt": "e",
         "domain": "finance", "reason": "r", "score": 0.5, "passages": []}
        for i in range(1, n + 1)
    ]


def test_scope_terms_drop_stopwords_and_repeats():
    assert assist.scope_terms("forget everything about my old 401(k) plan, my 401k plan") == ["401", "plan", "401k"]


def test_groups_keep_only_given_numbers_once_and_collect_the_rest():
    cands = _cands(4)
    raw = json.dumps({"groups": [
        {"title": "401(k) statements", "explanation": "They are the plan's statements.", "items": [1, 2, 99, 2]},
        {"title": "Probably unrelated", "explanation": "", "items": [2, "3", 0]},
    ]})
    groups = assist.parse_groups(raw, cands)
    assert [(g["title"], [c["id"] for c in g["items"]]) for g in groups] == [
        ("401(k) statements", ["x1", "x2"]),
        ("Other matches", ["x3", "x4"]),
    ]


def test_a_reply_without_groups_is_an_error():
    with pytest.raises(ValueError):
        assist.parse_groups('{"answer": 1}', _cands(1))


@pytest.fixture
def llm(monkeypatch):
    calls: list[str] = []
    state: dict[str, Any] = {"local": "ok"}

    async def fake(messages, *, stage=None, **kw):
        calls.append(stage)
        if stage == "forget_assist" and state["local"] != "ok":
            raise RuntimeError("Local inference provider is unavailable")
        return json.dumps({"groups": [{"title": "All", "explanation": "matches", "items": [1]}]})

    monkeypatch.setattr("core.utils.internal_llm.call_internal_llm", fake)
    monkeypatch.setattr(assist, "cloud_model", lambda: "cloud/model-x")
    monkeypatch.setattr("config.CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
    monkeypatch.setattr("app.services.private_mode.get_private_mode_level", lambda: 0)
    return {"calls": calls, "state": state}


@pytest.mark.asyncio
async def test_groups_on_the_local_model(llm):
    out = await assist.group_candidates("my 401k", _cands(2))
    assert out["status"] == "grouped" and out["model"] == "local"
    assert llm["calls"] == ["forget_assist"]
    assert [g["title"] for g in out["groups"]] == ["All", "Other matches"]


@pytest.mark.asyncio
async def test_asks_for_consent_when_the_local_model_is_down(llm):
    llm["state"]["local"] = "down"
    out = await assist.group_candidates("my 401k", _cands(2))
    assert out["status"] == "needs_consent" and out["cloud_model"] == "cloud/model-x"
    assert llm["calls"] == ["forget_assist"]
    assert [c["id"] for g in out["groups"] for c in g["items"]] == ["x1", "x2"]


@pytest.mark.asyncio
async def test_sends_to_the_cloud_only_with_consent(llm):
    llm["state"]["local"] = "down"
    out = await assist.group_candidates("my 401k", _cands(2), allow_cloud=True)
    assert out["status"] == "grouped" and out["model"] == "cloud"
    assert llm["calls"] == ["forget_assist_cloud"]


@pytest.mark.asyncio
@pytest.mark.parametrize("setup", ["local-only", "private"])
async def test_the_cloud_is_refused_under_local_only_and_private_mode(llm, monkeypatch, setup):
    llm["state"]["local"] = "down"
    if setup == "local-only":
        monkeypatch.setattr("config.CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)
    else:
        monkeypatch.setattr("app.services.private_mode.get_private_mode_level", lambda: 1)
    consented = await assist.group_candidates("my 401k", _cands(2), allow_cloud=True)
    assert consented["status"] == "ungrouped" and consented["reason"]
    assert "forget_assist_cloud" not in llm["calls"]
    asked = await assist.group_candidates("my 401k", _cands(2))
    assert asked["status"] == "ungrouped" and asked["cloud_model"] == ""


# ---- candidate search ----

class _Rec(dict):
    def __getitem__(self, key: str) -> Any:
        return dict.get(self, key)


@pytest.fixture
def stores(tmp_path: Path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    results = [
        {"artifact_id": A, "filename": "401k-2019.pdf", "domain": "finance", "chunk_id": f"{A}_1111111111111111",
         "parent_chunk_id": "", "content": "Source: x | Domain: finance\n\nThe 401(k) plan statement", "relevance": 0.9},
        {"artifact_id": A, "filename": "401k-2019.pdf", "domain": "finance", "chunk_id": f"{A}_2222222222222222",
         "parent_chunk_id": f"{A}_3333333333333333", "content": "rollover", "relevance": 0.6},
        {"artifact_id": "community:7", "chunk_id": "community:7", "content": "summary", "relevance": 0.8},
    ]
    monkeypatch.setattr("core.agents.query_agent.multi_domain_query", AsyncMock(return_value=results))
    monkeypatch.setattr("app.deps.get_chroma", MagicMock())
    monkeypatch.setattr("utils.domain_privacy.owner_domains", lambda: ["finance", "conversations", "projects"])
    monkeypatch.setattr("utils.domain_privacy.sensitive_domains_opted_in", lambda: False)
    monkeypatch.setattr(
        "core.retrieval.graphrag_retriever.entity_neighborhood_artifact_ids", lambda d, ids, top_k: [(B, 3)],
    )
    session = MagicMock()
    session.run.return_value = [_Rec(id=B, filename="employer-benefits.md", domain="finance", summary="benefits")]
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr("app.deps.get_neo4j", lambda: driver)
    monkeypatch.setattr(assist, "_memories", lambda scope: [
        {"kind": "memory", "id": "v1", "store": "memories", "label": "Has a 401(k) at Acme", "domain": "conversations",
         "excerpt": "", "reason": "", "score": 0.7, "passages": []},
    ])
    monkeypatch.setattr("core.agents.memory.recall_memories", AsyncMock(side_effect=AssertionError("recall reinforces")))
    convos = [
        {"id": "c1", "title": "Rolling over the old 401k plan", "messages": [{"content": "which plan?"}]},
        {"id": "c2", "title": "Groceries", "messages": [{"content": "eggs and milk"}]},
        {"id": "c3", "title": "401k plan question", "messages": []},
    ]
    monkeypatch.setattr("app.sync.user_state.read_conversations", lambda sd: convos)
    return {"reg": reg, "results": results}


@pytest.mark.asyncio
async def test_finds_documents_neighbours_memories_and_conversations(stores):
    cands = await assist.find_candidates("my old 401k plan")
    by_kind = [(c["kind"], c["id"]) for c in cands]
    assert by_kind == [("artifact", A), ("artifact", B), ("memory", "v1"), ("conversation", "c1"), ("conversation", "c3")]
    doc = cands[0]
    assert [p["id"] for p in doc["passages"]] == [f"{A}_1111111111111111", f"{A}_3333333333333333"]
    assert doc["excerpt"] == "The 401(k) plan statement"
    assert "3 of the same" in cands[1]["reason"]


@pytest.mark.asyncio
async def test_the_search_skips_conversations_domain_and_forgotten_items(stores):
    from core.agents import query_agent

    stores["reg"].append([
        Entry("fg_1", Subject("conversation", "c3"), "trashed", "2026-10-09T10:00:00Z", "m1", "ui"),
        Entry("fg_1", Subject("artifact", B), "trashed", "2026-10-09T10:00:00Z", "m1", "ui"),
    ])
    cands = await assist.find_candidates("my old 401k plan")
    assert ("conversation", "c3") not in [(c["kind"], c["id"]) for c in cands]
    assert ("artifact", B) not in [(c["kind"], c["id"]) for c in cands]
    assert query_agent.multi_domain_query.call_args.kwargs["domains"] == ["finance", "projects"]


@pytest.mark.asyncio
async def test_a_domain_scoped_search_covers_only_those_domains(stores):
    cands = await assist.find_candidates("my old 401k plan", domains=["projects"])
    assert {c["store"] for c in cands} <= {"knowledge_base"}


def test_conversation_terms_match_whole_words(stores, monkeypatch):
    monkeypatch.setattr("app.sync.user_state.read_conversations", lambda sd: [
        {"id": "c-syntax", "title": "Python syntax errors", "messages": []},
        {"id": "c-tax", "title": "My tax return", "messages": [{"content": "the tax bill"}]},
    ])
    assert [c["id"] for c in assist._conversations(["tax"])] == ["c-tax"]

