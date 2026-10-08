# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The forget engine: trash hides, restore un-hides, purge erases with a receipt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.forget import engine
from app.services.forget.adapters import PurgeResult
from core.forget.registry import Registry, Subject


class FakeAdapter:
    def __init__(self, name, fail_purge=False):
        self.name, self.kinds = name, frozenset({"conversation"})
        self.calls: list[tuple[str, str]] = []
        self.fail_purge = fail_purge

    def hide(self, subject, forget_id):
        self.calls.append(("hide", subject.id))

    def restore(self, subject, forget_id):
        self.calls.append(("restore", subject.id))

    def purge(self, subject):
        self.calls.append(("purge", subject.id))
        if self.fail_purge:
            raise RuntimeError("store down")
        return PurgeResult(removed=2)


@pytest.fixture()
def wired(tmp_path: Path, monkeypatch):
    reg = Registry(tmp_path / "forget", "m1")
    a, b = FakeAdapter("derived"), FakeAdapter("record")
    monkeypatch.setattr(engine, "get_registry", lambda: reg)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, b])
    monkeypatch.setattr(engine, "_receipts_dir", lambda: tmp_path / "forget" / "receipts")
    monkeypatch.setattr(engine, "_applied_path", lambda: tmp_path / "applied.jsonl")
    monkeypatch.setattr(engine, "_audit", lambda *args, **kwargs: None)
    return reg, a, b, tmp_path


def test_trash_hides_through_every_adapter_and_records_trashed(wired):
    reg, a, b, _ = wired
    fid = engine.trash([Subject("conversation", "c1")], requested_by="ui")
    assert reg.state_of("conversation", "c1") == "trashed"
    assert ("hide", "c1") in a.calls and ("hide", "c1") in b.calls
    assert fid.startswith("fg_")


def test_restore_unhides_and_clears(wired):
    reg, a, _, _ = wired
    fid = engine.trash([Subject("conversation", "c1")], requested_by="ui")
    assert engine.restore(fid) == 1
    assert reg.state_of("conversation", "c1") == "restored"
    assert ("restore", "c1") in a.calls


def test_restore_after_purge_is_refused(wired):
    _, _, _, _ = wired
    fid = engine.trash([Subject("conversation", "c1")], requested_by="ui")
    engine.purge(fid)
    with pytest.raises(engine.ForgetConflict):
        engine.restore(fid)


def test_purge_runs_adapters_in_order_and_writes_a_content_free_receipt(wired, monkeypatch):
    reg, a, b, tmp = wired
    order: list[str] = []
    for adapter in (a, b):
        original = adapter.purge
        def tracked(subject, _orig=original, _name=adapter.name):
            order.append(_name)
            return _orig(subject)
        monkeypatch.setattr(adapter, "purge", tracked)
    receipt = engine.forget_permanently([Subject("conversation", "c1")], requested_by="api")
    assert reg.state_of("conversation", "c1") == "purged"
    assert order == ["derived", "record"]
    on_disk = json.loads((tmp / "forget" / "receipts" / f"{receipt['forget_id']}.json").read_text())
    assert on_disk["adapters"]["derived"] == {"status": "done", "removed": 2}
    assert "out_of_reach" in on_disk and on_disk["subjects"] == [{"kind": "conversation", "id": "c1"}]
    assert "c1" not in json.dumps(on_disk["adapters"])


def test_a_failing_adapter_leaves_the_subject_trashed_and_the_receipt_pending(wired, monkeypatch):
    reg, a, _, _ = wired
    broken = FakeAdapter("record", fail_purge=True)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, broken])
    receipt = engine.forget_permanently([Subject("conversation", "c1")], requested_by="api")
    assert reg.state_of("conversation", "c1") == "trashed"
    assert receipt["adapters"]["record"]["status"] == "pending"


def test_empty_trash_purges_only_entries_older_than_the_window(wired):
    from core.forget.registry import Entry
    reg, _, _, _ = wired
    reg.append([
        Entry("fg_old", Subject("conversation", "old"), "trashed", "2026-08-01T00:00:00Z", "m1", "ui"),
        Entry("fg_new", Subject("conversation", "new"), "trashed", "2099-01-01T00:00:00Z", "m1", "ui"),
    ])
    assert engine.empty_trash(older_than_days=30) == ["fg_old"]
    assert reg.state_of("conversation", "old") == "purged"
    assert reg.state_of("conversation", "new") == "trashed"


def test_every_action_is_audited_without_content(wired, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(engine, "_audit", lambda action, **kw: calls.append((action, kw)))
    # Subject ids that cannot occur inside a hex forget id, so the check cannot pass or fail by chance.
    fid = engine.trash([Subject("conversation", "conv-one")], requested_by="ui")
    engine.restore(fid)
    engine.forget_permanently([Subject("conversation", "conv-two")], requested_by="api")
    assert [c[0] for c in calls] == ["forget.trash", "forget.restore", "forget.trash", "forget.purge"]
    assert all("conv-one" not in repr(c) and "conv-two" not in repr(c) for c in calls)


def test_restore_after_a_partly_failed_purge_is_refused(wired, monkeypatch):
    reg, a, _, _ = wired
    broken = FakeAdapter("record", fail_purge=True)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, broken])
    receipt = engine.forget_permanently([Subject("conversation", "c1")], requested_by="api")
    assert reg.state_of("conversation", "c1") == "trashed"
    with pytest.raises(engine.ForgetConflict):
        engine.restore(receipt["forget_id"])
    assert reg.state_of("conversation", "c1") == "trashed"
    assert ("restore", "c1") not in a.calls


def test_a_retried_purge_keeps_the_counts_of_the_first_attempt(wired, monkeypatch):
    reg, a, _, _ = wired
    flaky = FakeAdapter("record", fail_purge=True)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, flaky])
    fid = engine.forget_permanently([Subject("conversation", "c1")], requested_by="api")["forget_id"]
    flaky.fail_purge = False
    receipt = engine.purge(fid)
    assert reg.state_of("conversation", "c1") == "purged"
    assert receipt["adapters"]["derived"] == {"status": "done", "removed": 4}
    assert receipt["adapters"]["record"] == {"status": "done", "removed": 2}


def test_empty_trash_retries_a_started_purge_whatever_its_age(wired, monkeypatch):
    reg, a, _, _ = wired
    flaky = FakeAdapter("record", fail_purge=True)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, flaky])
    fid = engine.forget_permanently([Subject("conversation", "c1")], requested_by="api")["forget_id"]
    flaky.fail_purge = False
    assert engine.empty_trash(older_than_days=30) == [fid]
    assert reg.state_of("conversation", "c1") == "purged"


def test_a_failed_hide_at_trash_is_retried_by_apply_remote(wired, monkeypatch):
    reg, a, _, _ = wired
    flaky = FakeAdapter("transcripts")
    failing = {"on": True}
    original_hide = flaky.hide

    def hide(subject, forget_id):
        original_hide(subject, forget_id)
        if failing["on"]:
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(flaky, "hide", hide)
    monkeypatch.setattr(engine, "_adapters_for", lambda kind: [a, flaky])
    engine.trash([Subject("conversation", "c1")], requested_by="ui")
    assert reg.state_of("conversation", "c1") == "trashed"
    failing["on"] = False

    assert engine.apply_remote()["hidden"] == 1
    assert flaky.calls.count(("hide", "c1")) == 2
    assert engine.apply_remote()["hidden"] == 0


def test_a_trash_whose_hides_all_succeeded_is_not_hidden_again(wired):
    _, a, _, _ = wired
    engine.trash([Subject("conversation", "c1")], requested_by="ui")
    assert engine.apply_remote()["hidden"] == 0
    assert a.calls.count(("hide", "c1")) == 1


def test_apply_remote_never_purges_for_a_readd(wired):
    from core.forget.registry import Entry
    reg, a, b, _ = wired
    reg.append([
        Entry("fg_r", Subject("conversation", "c7"), "purged", "2026-10-07T10:00:00Z", "m2", "ui"),
        Entry("fg_r", Subject("conversation", "c7"), "readded", "2026-10-07T11:00:00Z", "m2", "ingest"),
    ])
    counts = engine.apply_remote()
    assert ("purge", "c7") not in a.calls and ("purge", "c7") not in b.calls
    assert counts["purged"] == 0
