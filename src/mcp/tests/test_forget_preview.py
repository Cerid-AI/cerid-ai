# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""What the delete dialog offers for a conversation, and its defaults."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.forget.registry import Entry, Subject
from tests.helpers.forget import isolate_forget

KB = "k" * 64
MEM_OWN, MEM_SHARED, SUMMARY = "a" * 64, "b" * 64, "c" * 64
VERIFIED = "11111111-2222-3333-4444-555555555555"


class _Graph:
    def __init__(self, derived, verified_ids, verified_rows, facts):
        self.derived, self.verified_ids, self.verified_rows, self.facts = derived, verified_ids, verified_rows, facts
        self.fact_aids: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        result = MagicMock()
        if "-[:EXTRACTED_FROM]->(:Conversation {id: $cid})" in query:
            rows = self.derived
        elif "VERIFIED_BY" in query:
            rows = [{"id": i} for i in self.verified_ids]
        elif "m.id IN $ids" in query:
            rows = [r for r in self.verified_rows if r["id"] in params["ids"]]
        elif "[:FACT]->(f:Fact)" in query:
            self.fact_aids = params["aids"]
            result.single.return_value = {"n": self.facts}
            return result
        else:
            rows = []
        result.__iter__.return_value = iter(rows)
        return result


def _driver(graph):
    driver = MagicMock()
    driver.session.return_value = graph
    return driver


@pytest.fixture()
def world(tmp_path: Path, monkeypatch):
    from app.sync.user_state import write_conversation

    reg = isolate_forget(monkeypatch, tmp_path)
    write_conversation(str(tmp_path), {
        "id": "c-1", "title": "Tax questions",
        "messages": [{"role": "assistant", "content": "x",
                      "sourcesUsed": [{"artifact_id": KB, "source_type": "kb", "filename": "report.pdf"},
                                      {"artifact_id": "web", "source_type": "external"}]}],
    })
    write_conversation(str(tmp_path), {
        "id": "c-2", "title": "Other",
        "messages": [{"role": "assistant", "sourcesUsed": [{"artifact_id": KB, "source_type": "kb"}]}],
    })
    graph = _Graph(
        derived=[
            {"id": MEM_OWN, "filename": "memory_fact_c-1", "summary": "Owns a 401(k)", "scope": "", "others": []},
            {"id": MEM_SHARED, "filename": "memory_fact_c-1b", "summary": "", "scope": "", "others": ["c-2"]},
            {"id": SUMMARY, "filename": "session_summary_c-1", "summary": "Summary", "scope": "session_summary", "others": []},
        ],
        verified_ids=[VERIFIED],
        verified_rows=[{"id": VERIFIED, "text": "The 2025 limit is $23,500."}],
        facts=3,
    )
    with patch("app.deps.get_neo4j", return_value=_driver(graph)), \
         patch("app.services.content_lifecycle.conversation_transcript_artifact_ids", return_value=["t" * 64]):
        yield reg, graph


def _group(preview, key):
    return next(g for g in preview["groups"] if g["key"] == key)


def test_preview_groups_derived_checked_and_cited_unchecked(world):
    from app.services.forget.preview import preview_conversation

    _, graph = world
    pv = preview_conversation("c-1")
    assert pv["title"] == "Tax questions"
    assert [g["key"] for g in pv["groups"]] == [
        "transcripts", "memories", "summary", "verified_memories", "cited_documents",
    ]
    assert _group(pv, "transcripts")["default"] == "always"
    memories = _group(pv, "memories")
    assert memories["default"] == "checked"
    own = next(i for i in memories["items"] if i["id"] == MEM_OWN)
    shared = next(i for i in memories["items"] if i["id"] == MEM_SHARED)
    assert own == {"kind": "artifact", "id": MEM_OWN, "label": "Owns a 401(k)", "shared_with": 0}
    assert shared["shared_with"] == 1 and shared["default"] == "unchecked"
    assert shared["label"] == "memory_fact_c-1b"
    assert [i["id"] for i in _group(pv, "summary")["items"]] == [SUMMARY]
    assert _group(pv, "verified_memories")["items"] == [
        {"kind": "memory", "id": VERIFIED, "label": "The 2025 limit is $23,500."},
    ]
    cited = _group(pv, "cited_documents")
    assert cited["default"] == "unchecked"
    assert cited["items"] == [{"kind": "artifact", "id": KB, "label": "report.pdf", "used_by": 1}]
    assert pv["derived_facts"] == 3
    assert set(graph.fact_aids) == {MEM_OWN, SUMMARY}, "facts are counted only for memories that would go"
    assert pv["out_of_reach"]


def test_preview_omits_items_already_forgotten(world):
    from app.services.forget.preview import preview_conversation

    reg, _ = world
    reg.append([
        Entry("fg_1", Subject("artifact", MEM_OWN), "trashed", "2026-10-08T10:00:00Z", "m1", "ui"),
        Entry("fg_1", Subject("memory", VERIFIED), "purged", "2026-10-08T10:00:00Z", "m1", "ui"),
        Entry("fg_2", Subject("conversation", "c-2"), "trashed", "2026-10-08T10:00:00Z", "m1", "ui"),
    ])
    pv = preview_conversation("c-1")
    assert [i["id"] for i in _group(pv, "memories")["items"]] == [MEM_SHARED]
    shared = _group(pv, "memories")["items"][0]
    assert shared["shared_with"] == 1, "a trashed conversation can be restored, so it still shares"
    assert _group(pv, "verified_memories")["items"] == []
    assert _group(pv, "cited_documents")["items"][0]["used_by"] == 0


def test_preview_of_a_conversation_the_server_never_saw_is_empty(tmp_path: Path, monkeypatch):
    from app.services.forget.preview import preview_conversation

    isolate_forget(monkeypatch, tmp_path)
    with patch("app.deps.get_neo4j", return_value=_driver(_Graph([], [], [], 0))), \
         patch("app.services.content_lifecycle.conversation_transcript_artifact_ids", return_value=[]):
        pv = preview_conversation("browser-only")
    assert pv["title"] == "" and all(not g["items"] for g in pv["groups"]) and pv["derived_facts"] == 0


def test_a_memory_shared_with_a_trashed_conversation_still_counts_as_shared(world):
    """A trashed conversation can be restored; taking its memory would be permanent."""
    from app.services.forget.preview import preview_conversation

    reg, _ = world
    reg.append([Entry("fg_2", Subject("conversation", "c-2"), "trashed", "2026-10-08T10:00:00Z", "m1", "ui")])
    shared = next(i for i in _group(preview_conversation("c-1"), "memories")["items"] if i["id"] == MEM_SHARED)
    assert shared["shared_with"] == 1 and shared["default"] == "unchecked"


def test_a_purged_sharer_no_longer_counts(world):
    from app.services.forget.preview import preview_conversation

    reg, _ = world
    reg.append([Entry("fg_2", Subject("conversation", "c-2"), "purged", "2026-10-08T10:00:00Z", "m1", "ui")])
    shared = next(i for i in _group(preview_conversation("c-1"), "memories")["items"] if i["id"] == MEM_SHARED)
    assert shared["shared_with"] == 0 and "default" not in shared


def test_conversations_forgotten_together_do_not_share_with_each_other(world):
    from app.services.forget.preview import preview_conversation

    pv = preview_conversation("c-1", forgetting=frozenset({"c-1", "c-2"}))
    shared = next(i for i in _group(pv, "memories")["items"] if i["id"] == MEM_SHARED)
    assert shared["shared_with"] == 0 and "default" not in shared
