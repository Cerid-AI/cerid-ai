# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Retrieval drops forgotten chunks, the children of a forgotten parent, and forgotten artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.forget.read_filter import drop_forgotten
from core.forget.registry import Entry, Registry, Subject


@pytest.fixture
def reg(tmp_path: Path, monkeypatch) -> Registry:
    registry = Registry(tmp_path / "forget", "m1")
    monkeypatch.setattr("core.forget.registry.get_registry", lambda: registry)
    return registry


def _forget(reg: Registry, kind: str, sid: str, state: str = "trashed", fid: str = "fg_1", at: str = "10") -> None:
    reg.append([Entry(fid, Subject(kind, sid), state, f"2026-10-09T{at}:00:00Z", "m1", "ui")])


def _results() -> list[dict[str, Any]]:
    return [
        {"chunk_id": "a_1", "artifact_id": "a", "parent_chunk_id": ""},
        {"chunk_id": "a_2", "artifact_id": "a", "parent_chunk_id": "a_p"},
        {"chunk_id": "b_1", "artifact_id": "b", "parent_chunk_id": ""},
        {"content": "external", "source_type": "web"},
    ]


def test_nothing_forgotten_keeps_everything(reg):
    assert drop_forgotten(_results()) == _results()


def test_a_forgotten_chunk_goes(reg):
    _forget(reg, "chunk", "a_1")
    assert [r.get("chunk_id") for r in drop_forgotten(_results())] == ["a_2", "b_1", None]


def test_children_of_a_forgotten_parent_go(reg):
    _forget(reg, "chunk", "a_p", state="purged")
    assert [r.get("chunk_id") for r in drop_forgotten(_results())] == ["a_1", "b_1", None]


def test_a_forgotten_artifact_takes_all_its_results(reg):
    _forget(reg, "artifact", "a")
    assert [r.get("chunk_id") for r in drop_forgotten(_results())] == ["b_1", None]


def test_a_restored_chunk_returns(reg):
    _forget(reg, "chunk", "a_1")
    _forget(reg, "chunk", "a_1", state="restored", at="11")
    assert len(drop_forgotten(_results())) == 4


def test_an_unreadable_registry_keeps_results(monkeypatch):
    def boom(kind: str) -> frozenset[str]:
        raise OSError("sync dir gone")
    monkeypatch.setattr("core.forget.read_filter.forgotten_ids", boom)
    assert len(drop_forgotten(_results())) == 4


def test_the_library_list_hides_trashed_artifacts():
    from app.db.neo4j.artifacts import count_artifacts, list_artifacts

    session = MagicMock()
    session.run.return_value = MagicMock(single=MagicMock(return_value={"total": 0}), __iter__=lambda s: iter([]))
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    list_artifacts(driver, include_forgotten=False)
    count_artifacts(driver, include_forgotten=False)
    queries = [c.args[0] for c in session.run.call_args_list]
    assert all("archived_reason, '') STARTS WITH 'forget:'" in q for q in queries)
    session.run.reset_mock()
    list_artifacts(driver)
    assert "forget:" not in session.run.call_args.args[0]


@pytest.mark.asyncio
async def test_multi_domain_query_drops_a_forgotten_passage(reg, monkeypatch):
    """Through the real per-domain arm, not the helper alone."""
    from core.agents import query_agent
    from core.retrieval import bm25 as bm25_mod
    from tests.test_consumer_record_types import _Chroma

    monkeypatch.setenv("CERID_FILTER_PENDING_CHUNKS", "false")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    monkeypatch.setattr(query_agent, "_parent_child_enabled", lambda: False)
    monkeypatch.setattr(query_agent, "_unsearchable_folder_ids", lambda: set())

    before = await query_agent.multi_domain_query(
        query="power bill", domains=["finance", "inbox"], top_k=5, chroma_client=_Chroma(),
    )
    assert "card" in {r["chunk_id"] for r in before}
    _forget(reg, "chunk", "card")
    after = await query_agent.multi_domain_query(
        query="power bill", domains=["finance", "inbox"], top_k=5, chroma_client=_Chroma(),
    )
    assert "card" not in {r["chunk_id"] for r in after}
    assert {r["chunk_id"] for r in after} == {r["chunk_id"] for r in before} - {"card"}
