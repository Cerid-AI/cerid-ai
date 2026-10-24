# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Verification reports are stored once, per message, in the graph (forget spec
§6.2): conversations give up the reports they embed, the server keeps the one
it has, and nothing is lost when the graph cannot take them."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.services import verification_reports as vr

CLAIM = {"claim": "The 2025 limit is $23,500.", "status": "verified", "similarity": 0.9,
         "verification_method": "kb", "source_artifact_id": "a" * 64}


def _report(*claims: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"conversation_id": "c1", "timestamp": "t", "skipped": False, "claims": list(claims),
            "summary": {"total": len(claims)}, **extra}


class _Graph:
    """The two graph calls the service makes, recorded."""

    def __init__(self, held: set[tuple[str, str]] | None = None, forgotten: bool = False):
        self.held = set(held or ())
        self.saved: list[dict[str, Any]] = []
        self.forgotten = forgotten

    def get(self, driver, cid, mid=None):
        return {"report_id": "r"} if (cid, mid) in self.held else None

    def save(self, driver, cid, claims, overall, **kw):
        if self.forgotten:
            return None
        self.saved.append({"cid": cid, "claims": claims, "overall": overall, **kw})
        self.held.add((cid, kw["message_id"]))
        return "rid"


@pytest.fixture()
def graph(monkeypatch):
    g = _Graph()
    monkeypatch.setattr("app.db.neo4j.artifacts.get_verification_report", g.get)
    monkeypatch.setattr("app.db.neo4j.artifacts.save_verification_report", g.save)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: "driver")
    return g


def test_a_conversation_gives_up_its_reports_to_the_graph(graph):
    convo = {"id": "c1", "messages": [], "verificationReports": {"m1": _report(CLAIM), "m2": _report(CLAIM, CLAIM)}}
    stored = vr.absorb_embedded_reports(convo)
    assert "verificationReports" not in stored and stored["id"] == "c1"
    assert sorted(s["message_id"] for s in graph.saved) == ["m1", "m2"]
    two = next(s for s in graph.saved if s["message_id"] == "m2")
    assert two["total"] == 2 and two["verified"] == 2  # counted from the claims, as a live report is


def test_the_graph_keeps_its_own_copy(graph):
    graph.held.add(("c1", "m1"))
    vr.absorb_embedded_reports({"id": "c1", "verificationReports": {"m1": _report(CLAIM)}})
    assert graph.saved == []


def test_the_legacy_single_report_belonged_to_the_last_assistant_message(graph):
    convo = {"id": "c1", "verificationReport": _report(CLAIM), "messages": [
        {"id": "u1", "role": "user"}, {"id": "a1", "role": "assistant"}, {"id": "a2", "role": "assistant"},
    ]}
    stored = vr.absorb_embedded_reports(convo)
    assert "verificationReport" not in stored
    assert [s["message_id"] for s in graph.saved] == ["a2"]


@pytest.mark.parametrize("mid,report", [
    ("m:1", _report(CLAIM)),               # not a message id
    ("m1", _report(CLAIM, skipped=True)),  # a skipped run
    ("m1", _report()),                     # no claims
    ("m1", "not a report"),
])
def test_nothing_is_kept_of_an_empty_or_malformed_report(graph, mid, report):
    assert vr.store_reports("c1", {mid: report}) == []
    assert graph.saved == []


def test_a_forgotten_conversation_keeps_nothing(monkeypatch):
    g = _Graph(forgotten=True)
    monkeypatch.setattr("app.db.neo4j.artifacts.get_verification_report", g.get)
    monkeypatch.setattr("app.db.neo4j.artifacts.save_verification_report", g.save)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: "driver")
    assert vr.store_reports("c1", {"m1": _report(CLAIM)}) == []


def test_when_the_graph_cannot_take_them_the_conversation_keeps_them(monkeypatch):
    monkeypatch.setattr("app.deps.get_neo4j", lambda: (_ for _ in ()).throw(RuntimeError("neo4j down")))
    convo = {"id": "c1", "verificationReports": {"m1": _report(CLAIM)}}
    assert vr.absorb_embedded_reports(convo) is convo


def test_a_conversation_without_reports_is_stored_as_it_is(graph):
    convo = {"id": "c1", "messages": []}
    assert vr.absorb_embedded_reports(convo) is convo


def test_saving_a_conversation_moves_its_reports_first(graph, tmp_path: Path, monkeypatch):
    from app.routers import user_state
    from app.sync.user_state import read_conversation
    from tests.helpers.forget import isolate_forget

    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(user_state, "private_blocks", lambda level: False)
    user_state.save_conversation({"id": "c1", "messages": [], "verificationReports": {"m1": _report(CLAIM)}})
    assert "verificationReports" not in read_conversation(str(tmp_path), "c1")
    assert [s["message_id"] for s in graph.saved] == ["m1"]


def test_the_boot_migration_rewrites_files_that_still_embed_reports(graph, tmp_path: Path, monkeypatch):
    from app.sync.user_state import read_conversation, write_conversation
    from tests.helpers.forget import isolate_forget

    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr("app.services.private_mode.private_blocks", lambda level: False)
    write_conversation(str(tmp_path), {"id": "c1", "messages": [], "verificationReports": {"m1": _report(CLAIM)}})
    write_conversation(str(tmp_path), {"id": "c2", "messages": []})
    assert vr.migrate_embedded_reports(str(tmp_path)) == {"conversations": 1, "kept": 0}
    assert "verificationReports" not in read_conversation(str(tmp_path), "c1")
    assert vr.migrate_embedded_reports(str(tmp_path)) == {"conversations": 0, "kept": 0}


def test_the_store_route_takes_nothing_under_private_mode(monkeypatch):
    import asyncio

    from app.routers import agents

    monkeypatch.setattr(agents, "saves_blocked", lambda: True)
    with patch("app.services.verification_reports.store_reports") as store:
        result = asyncio.run(agents.store_verification_reports.__wrapped__(
            "c1", agents.StoreReportsRequest(reports={"m1": _report(CLAIM)}),
        ))
    assert result == {"stored": []}
    store.assert_not_called()


def test_a_stored_report_keeps_its_skipped_count():
    from app.db.neo4j.artifacts import client_report

    report = client_report({"conversation_id": "c1", "message_id": "m1", "total": 3, "verified": 2,
                            "claims": [CLAIM, CLAIM, {**CLAIM, "status": "skipped"}]})
    assert report["summary"]["skipped"] == 1 and report["summary"]["total"] == 3


def test_an_id_in_the_per_message_namespace_names_no_report():
    """A conversation-level key cannot alias another conversation's
    per-message report (and so bypass its forget check)."""
    from core.agents.hallucination.persistence import get_hallucination_report, message_reports_prefix, report_key

    alias = message_reports_prefix("victim").removeprefix("hall:") + "m1"
    with pytest.raises(ValueError):
        report_key(alias)
    redis = __import__("fakeredis").FakeRedis(decode_responses=True)
    redis.set(report_key("victim", "m1"), '{"claims": ["secret"]}')
    assert get_hallucination_report(redis, alias) is None


def test_a_report_with_too_many_claims_is_not_taken(graph):
    assert vr.store_reports("c1", {"m1": _report(*([CLAIM] * (vr.MAX_CLAIMS + 1)))}) == []
    assert graph.saved == []


def test_a_forget_during_the_move_wins_over_the_save(graph, tmp_path: Path, monkeypatch):
    from fastapi import HTTPException

    from app.routers import user_state

    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))
    monkeypatch.setattr(user_state, "private_blocks", lambda level: False)
    checks = iter([False, True])  # live when the save starts, forgotten once the reports moved
    monkeypatch.setattr(user_state, "_forgotten", lambda cid: next(checks))
    with pytest.raises(HTTPException) as exc:
        user_state.save_conversation({"id": "c1", "messages": [], "verificationReports": {"m1": _report(CLAIM)}})
    assert exc.value.status_code == 410
    assert not (tmp_path / "user" / "conversations" / "c1.json").exists()


def test_the_boot_migration_leaves_a_file_that_changed_meanwhile(graph, tmp_path: Path, monkeypatch):
    from app.sync.user_state import read_conversation, write_conversation
    from tests.helpers.forget import isolate_forget

    isolate_forget(monkeypatch, tmp_path)
    monkeypatch.setattr("app.services.private_mode.private_blocks", lambda level: False)
    write_conversation(str(tmp_path), {"id": "c1", "messages": [], "verificationReports": {"m1": _report(CLAIM)}})
    real_store = vr.store_reports

    def save_lands_meanwhile(cid, reports):
        held = real_store(cid, reports)
        write_conversation(str(tmp_path), {"id": "c1", "messages": [{"id": "new"}]})  # a client saved
        return held

    monkeypatch.setattr(vr, "store_reports", save_lands_meanwhile)
    assert vr.migrate_embedded_reports(str(tmp_path)) == {"conversations": 0, "kept": 1}
    assert read_conversation(str(tmp_path), "c1")["messages"] == [{"id": "new"}]
