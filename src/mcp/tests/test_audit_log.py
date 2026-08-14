# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the Enterprise append-only audit log.

The guarantee being sold is not "events are written" — it is that a *changed*
log can be told from an unchanged one. So most of what is pinned here is
tampering: modify a field, delete a line, insert one, reorder two, corrupt one,
and check that ``verify()`` names the sequence where it happened. A verifier
that returns ok on any of those is worth nothing, and would look identical from
the outside to one that works.
"""
from __future__ import annotations

import json

import pytest

from core.utils import audit_log


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    """Point the log at a temp DATA_DIR and reset the process-wide counter."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(audit_log, "_write_failures", 0, raising=False)
    yield


def _lines() -> list[dict]:
    out: list[dict] = []
    for path in audit_log.segments():
        out.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return out


def _rewrite(records: list[dict]) -> None:
    """Replace the single segment with exactly these records."""
    path = audit_log.segments()[0]
    path.write_text(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
        )
    )


class TestRecording:
    def test_writes_a_record_and_returns_it(self):
        entry = audit_log.record("license.activate", actor="operator", target="key-123")
        assert entry["seq"] == 0
        assert entry["action"] == "license.activate"
        assert entry["actor"] == "operator"
        assert entry["target"] == "key-123"
        assert entry["outcome"] == "success"
        assert entry["prev"] == audit_log.GENESIS
        assert len(entry["hash"]) == 64

    def test_sequence_and_chain_advance(self):
        first = audit_log.record("a.one")
        second = audit_log.record("a.two")
        assert (first["seq"], second["seq"]) == (0, 1)
        assert second["prev"] == first["hash"]
        assert second["hash"] != first["hash"]

    def test_two_records_with_identical_content_still_differ(self):
        # They chain differently and carry different timestamps, so an attacker
        # cannot swap one for the other.
        one = audit_log.record("a.same", detail={"x": 1})
        two = audit_log.record("a.same", detail={"x": 1})
        assert one["hash"] != two["hash"]

    def test_rejects_an_empty_action(self):
        with pytest.raises(ValueError):
            audit_log.record("")

    def test_survives_a_process_restart(self):
        # The chain lives on disk, not in memory: the next record has to pick up
        # the previous hash by reading the file back.
        audit_log.record("a.one")
        first = _lines()[0]
        second = audit_log.record("a.two")
        assert second["prev"] == first["hash"]

    def test_refuses_to_restart_the_chain_when_the_tail_is_unreadable(self):
        # Treating an unreadable tail as an empty log would silently start a
        # second chain and orphan everything already recorded — the log would
        # keep working and verification would report a break forever after.
        audit_log.record("a.one")
        path = audit_log.segments()[0]
        path.write_text("{not json\n")
        with pytest.raises(audit_log.AuditLogError):
            audit_log.record("a.two")


class TestAuditHelper:
    def test_returns_true_and_writes(self):
        assert audit_log.audit("a.one") is True
        assert audit_log.count() == 1

    def test_reports_a_failure_instead_of_raising(self, monkeypatch):
        def boom(*_a, **_k):
            raise audit_log.AuditLogError("disk full")

        monkeypatch.setattr(audit_log, "record", boom)
        assert audit_log.audit("a.one") is False
        assert audit_log.write_failure_count() == 1

    def test_a_dropped_write_is_visible_rather_than_silent(self, monkeypatch):
        # The whole reason audit() does not raise. If the counter did not move,
        # a log that stopped recording would look exactly like a quiet system.
        monkeypatch.setattr(
            audit_log, "record", lambda *_a, **_k: (_ for _ in ()).throw(OSError("nope"))
        )
        audit_log.audit("a.one")
        audit_log.audit("a.two")
        assert audit_log.write_failure_count() == 2


class TestVerify:
    def test_an_untouched_chain_verifies(self):
        for i in range(5):
            audit_log.record(f"a.{i}")
        result = audit_log.verify()
        assert result["ok"] is True
        assert result["checked"] == 5
        assert result["broken_at"] is None

    def test_an_empty_log_reports_zero_rather_than_a_clean_bill(self):
        result = audit_log.verify()
        assert result["ok"] is True
        assert result["records"] == 0

    def test_detects_a_modified_field(self):
        audit_log.record("a.one", actor="attacker")
        audit_log.record("a.two")
        records = _lines()
        records[0]["actor"] = "someone-else"
        _rewrite(records)

        result = audit_log.verify()
        assert result["ok"] is False
        assert result["broken_at"] == 0
        assert "modified" in result["reason"]

    def test_detects_a_deleted_record(self):
        # The realistic attack: remove the one line that records what you did.
        for i in range(4):
            audit_log.record(f"a.{i}")
        records = _lines()
        del records[2]
        _rewrite(records)

        result = audit_log.verify()
        assert result["ok"] is False
        assert result["broken_at"] == 2
        assert "removed or inserted" in result["reason"]

    def test_detects_truncation_from_the_end(self):
        # Chaining alone cannot see this: what is left is a perfectly valid
        # shorter chain, because the last record has no successor to vouch for
        # it. The head sidecar is that successor, and this is the test that
        # justifies its existence.
        for i in range(4):
            audit_log.record(f"a.{i}")
        records = _lines()
        _rewrite(records[:2])

        result = audit_log.verify()
        assert result["ok"] is False
        assert "removed from the end" in result["reason"]

    def test_a_missing_head_is_reported_on_a_non_empty_log(self):
        audit_log.record("a.one")
        audit_log._head_path().unlink()

        result = audit_log.verify()
        assert result["ok"] is False
        assert "head marker is missing" in result["reason"]

    def test_a_head_pointing_at_a_different_record_is_reported(self):
        # Editing the log and the head to disagree — or editing only one of them.
        audit_log.record("a.one")
        audit_log._head_path().write_text('{"seq": 0, "hash": "deadbeef"}')

        assert audit_log.verify()["ok"] is False

    def test_the_head_advances_with_every_record(self):
        for i in range(3):
            written = audit_log.record(f"a.{i}")
        head = json.loads(audit_log._head_path().read_text())
        assert head == {"seq": written["seq"], "hash": written["hash"]}

    def test_detects_a_reordered_pair(self):
        for i in range(3):
            audit_log.record(f"a.{i}")
        records = _lines()
        records[0], records[1] = records[1], records[0]
        _rewrite(records)

        assert audit_log.verify()["ok"] is False

    def test_detects_a_re_chained_forgery(self):
        # An attacker who edits a record and recomputes ITS hash still leaves
        # the next record's `prev` pointing at the old one.
        audit_log.record("a.one")
        audit_log.record("a.two")
        records = _lines()
        records[0]["detail"] = {"tampered": True}
        records[0]["hash"] = audit_log.compute_hash(records[0])
        _rewrite(records)

        result = audit_log.verify()
        assert result["ok"] is False
        assert result["broken_at"] == 1
        assert "chain" in result["reason"]

    def test_detects_a_corrupted_line(self):
        audit_log.record("a.one")
        path = audit_log.segments()[0]
        path.write_text(path.read_text() + "{ garbage\n")

        result = audit_log.verify()
        assert result["ok"] is False
        assert "not valid JSON" in result["reason"]

    def test_detects_a_missing_hash(self):
        audit_log.record("a.one")
        records = _lines()
        del records[0]["hash"]
        _rewrite(records)

        assert audit_log.verify()["ok"] is False


class TestReading:
    def test_returns_newest_first(self):
        for i in range(3):
            audit_log.record(f"a.{i}")
        actions = [r["action"] for r in audit_log.read()]
        assert actions == ["a.2", "a.1", "a.0"]

    def test_filters_by_action_prefix(self):
        audit_log.record("license.activate")
        audit_log.record("artifact.delete")
        audit_log.record("license.deactivate")
        actions = [r["action"] for r in audit_log.read(action_prefix="license.")]
        assert actions == ["license.deactivate", "license.activate"]

    def test_filters_by_outcome(self):
        audit_log.record("auth.login", outcome="success")
        audit_log.record("auth.login", outcome="denied")
        assert len(audit_log.read(outcome="denied")) == 1

    def test_paginates(self):
        for i in range(10):
            audit_log.record(f"a.{i}")
        page = audit_log.read(limit=3, offset=3)
        assert [r["action"] for r in page] == ["a.6", "a.5", "a.4"]

    def test_caps_an_absurd_limit(self):
        audit_log.record("a.one")
        assert len(audit_log.read(limit=10_000)) == 1

    def test_surfaces_a_malformed_line_rather_than_skipping_it(self):
        # Skipping it makes a corrupted log read as a shorter clean one.
        audit_log.record("a.one")
        path = audit_log.segments()[0]
        path.write_text(path.read_text() + "{ garbage\n")

        rows = audit_log.read()
        assert any("malformed" in r for r in rows)
        assert len(rows) == 2


class TestSegments:
    def test_rolls_to_a_new_segment_past_the_size_limit(self, monkeypatch):
        monkeypatch.setenv("CERID_AUDIT_LOG_MAX_SEGMENT_BYTES", "400")
        for i in range(10):
            audit_log.record(f"a.{i}")
        assert len(audit_log.segments()) > 1

    def test_the_chain_continues_across_a_segment_boundary(self, monkeypatch):
        monkeypatch.setenv("CERID_AUDIT_LOG_MAX_SEGMENT_BYTES", "400")
        for i in range(10):
            audit_log.record(f"a.{i}")
        assert len(audit_log.segments()) > 1
        result = audit_log.verify()
        assert result["ok"] is True
        assert result["checked"] == 10

    def test_reading_spans_every_segment(self, monkeypatch):
        monkeypatch.setenv("CERID_AUDIT_LOG_MAX_SEGMENT_BYTES", "400")
        for i in range(10):
            audit_log.record(f"a.{i}")
        assert audit_log.count() == 10
        assert len(audit_log.read(limit=100)) == 10
