# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Which conversation a memory came from and a chat upload was dropped in
travels with sync (forget spec §6.6), so another machine's delete dialog can
offer them too."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import app.sync.import_ as import_mod
from app.sync._helpers import CONVERSATION_EDGES_JSONL, NEO4J_SUBDIR


def _driver(session: MagicMock) -> MagicMock:
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    return driver


def test_the_export_carries_memory_and_upload_edges(tmp_path):
    from app.sync.export import export_conversation_edges

    session = MagicMock()
    session.run.return_value = iter([
        {"source_id": "m1", "rel_type": "EXTRACTED_FROM", "target_id": "c1", "props": {}},
        {"source_id": "f1", "rel_type": "UPLOADED_IN", "target_id": "c1", "props": {"existing": False}},
    ])
    assert export_conversation_edges(_driver(session), sync_dir=str(tmp_path)) == {"conversation_edges": 2}
    assert set(session.run.call_args.kwargs["types"]) == {"EXTRACTED_FROM", "UPLOADED_IN"}
    rows = [json.loads(line) for line in (tmp_path / NEO4J_SUBDIR / CONVERSATION_EDGES_JSONL).read_text().splitlines()]
    assert [r["rel_type"] for r in rows] == ["EXTRACTED_FROM", "UPLOADED_IN"]


def test_the_import_takes_only_edges_between_live_things_here(tmp_path, monkeypatch):
    out = tmp_path / NEO4J_SUBDIR
    out.mkdir(parents=True)
    rows = [
        {"source_id": "f1", "rel_type": "UPLOADED_IN", "target_id": "c1", "props": {"existing": True, "at": "t"}},
        {"source_id": "gone", "rel_type": "UPLOADED_IN", "target_id": "c1", "props": {}},       # artifact forgotten
        {"source_id": "f1", "rel_type": "UPLOADED_IN", "target_id": "forgotten", "props": {}},  # chat forgotten
        {"source_id": "f1", "rel_type": "OWNS", "target_id": "c1", "props": {}},                # not a known type
        {"source_id": "absent", "rel_type": "EXTRACTED_FROM", "target_id": "c1", "props": {}},  # not on this machine
    ]
    (out / CONVERSATION_EDGES_JSONL).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(import_mod, "_is_forgotten_artifact", lambda aid: aid == "gone")
    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, i: kind == "conversation" and i == "forgotten")
    session = MagicMock()
    writes: list[tuple[str, dict[str, Any]]] = []

    def _run(cypher: str, **kw: Any) -> Any:
        writes.append((cypher, kw))
        return SimpleNamespace(single=lambda: {"n": 0 if kw.get("aid") == "absent" else 1})

    session.run.side_effect = _run
    assert import_mod.import_conversation_edges(_driver(session), sync_dir=str(tmp_path)) == {
        "edges_merged": 1, "edges_skipped": 4,
    }
    applied = [(c, kw) for c, kw in writes if kw["aid"] == "f1"]
    ((cypher, kw),) = applied
    assert "[r:UPLOADED_IN]" in cypher and kw["props"] == {"existing": True, "at": "t"}
