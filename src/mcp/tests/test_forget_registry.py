# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The forget registry: append-only, per-machine files, purged dominates."""
from __future__ import annotations

import json
from pathlib import Path

from core.forget.registry import Entry, Registry, Subject, new_forget_id


def _e(fid, kind, sid, state, at, machine="m1"):
    return Entry(fid, Subject(kind, sid), state, at, machine, "test")


def test_trash_then_restore_is_not_forgotten(tmp_path: Path):
    reg = Registry(tmp_path, "m1")
    reg.append([_e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z")])
    assert reg.is_forgotten("conversation", "c1")
    reg.append([_e("fg_1", "conversation", "c1", "restored", "2026-10-07T10:01:00Z")])
    assert reg.state_of("conversation", "c1") == "restored"
    assert not reg.is_forgotten("conversation", "c1")


def test_purged_dominates_a_later_restore_from_a_skewed_clock(tmp_path: Path):
    a, b = Registry(tmp_path, "mA"), Registry(tmp_path, "mB")
    a.append([_e("fg_1", "conversation", "c1", "purged", "2026-10-07T12:00:00Z", "mA")])
    b.append([_e("fg_1", "conversation", "c1", "restored", "2026-10-07T13:00:00Z", "mB")])
    assert Registry(tmp_path, "mC").state_of("conversation", "c1") == "purged"


def test_each_machine_writes_only_its_own_file(tmp_path: Path):
    Registry(tmp_path, "mA").append([_e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z", "mA")])
    Registry(tmp_path, "mB").append([_e("fg_2", "conversation", "c2", "trashed", "2026-10-07T10:00:00Z", "mB")])
    assert sorted(p.name for p in tmp_path.glob("registry*.jsonl")) == ["registry-mA.jsonl", "registry-mB.jsonl"]
    reader = Registry(tmp_path, "mC")
    assert reader.is_forgotten("conversation", "c1") and reader.is_forgotten("conversation", "c2")


def test_malformed_and_half_written_lines_are_skipped_not_fatal(tmp_path: Path):
    good = _e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z").to_json()
    (tmp_path / "registry-mA.jsonl").write_text(
        json.dumps(good) + "\n" + "{not json\n" + json.dumps({"forget_id": "x"}) + "\n" + '{"forget_id": "fg_2", "subj'
    )
    reg = Registry(tmp_path, "mB")
    assert reg.is_forgotten("conversation", "c1")
    assert reg.state_of("conversation", "c2") is None


def test_a_second_process_sees_new_entries_without_restart(tmp_path: Path):
    reader = Registry(tmp_path, "m1")
    assert not reader.is_forgotten("conversation", "c9")
    Registry(tmp_path, "m2").append([_e("fg_9", "conversation", "c9", "trashed", "2026-10-07T10:00:00Z", "m2")])
    assert reader.is_forgotten("conversation", "c9")


def test_latest_since_returns_one_entry_per_subject(tmp_path: Path):
    reg = Registry(tmp_path, "m1")
    reg.append([
        _e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z"),
        _e("fg_1", "conversation", "c1", "purged", "2026-10-07T11:00:00Z"),
        _e("fg_2", "conversation", "c2", "trashed", "2026-10-07T09:00:00Z"),
    ])
    latest = reg.latest(since="2026-10-07T09:30:00Z")
    assert [(e.subject.id, e.state) for e in latest] == [("c1", "purged")]


def test_no_sync_dir_means_nothing_is_forgotten_and_append_refuses(tmp_path: Path):
    reg = Registry(None, "m1")
    assert not reg.is_forgotten("conversation", "c1")
    try:
        reg.append([_e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z")])
    except RuntimeError as exc:
        assert "sync dir" in str(exc)
    else:
        raise AssertionError("append without a sync dir must refuse")


def test_forget_ids_are_unique_and_prefixed():
    ids = {new_forget_id() for _ in range(200)}
    assert len(ids) == 200 and all(i.startswith("fg_") and len(i) == 19 for i in ids)


def test_an_append_after_a_torn_write_keeps_the_new_entry(tmp_path: Path):
    good = _e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z").to_json()
    (tmp_path / "registry-m1.jsonl").write_text(json.dumps(good) + "\n" + '{"forget_id": "fg_x", "subj')
    reg = Registry(tmp_path, "m1")
    reg.append([_e("fg_2", "conversation", "c2", "trashed", "2026-10-07T10:05:00Z")])
    assert reg.is_forgotten("conversation", "c1") and reg.is_forgotten("conversation", "c2")


def test_undecodable_bytes_in_one_line_do_not_hide_the_others(tmp_path: Path):
    good = _e("fg_1", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z").to_json()
    (tmp_path / "registry-mA.jsonl").write_bytes(b'{"forget_id": "\xff\xfe\n' + json.dumps(good).encode() + b"\n")
    assert Registry(tmp_path, "mB").is_forgotten("conversation", "c1")


def test_a_readd_cancels_the_forget_it_names_whatever_the_clocks_say(tmp_path: Path):
    a, b = Registry(tmp_path, "mA"), Registry(tmp_path, "mB")
    a.append([_e("fg_A", "artifact", "x", "purged", "2026-10-07T12:00:00Z", "mA")])
    # Machine B's clock is hours behind: the re-add is stamped before the purge.
    b.append([_e("fg_A", "artifact", "x", "readded", "2026-10-07T08:00:00Z", "mB")])
    reader = Registry(tmp_path, "mC")
    assert reader.state_of("artifact", "x") == "readded"
    assert not reader.is_forgotten("artifact", "x")


def test_forget_readd_forget_again_ends_forgotten(tmp_path: Path):
    reg = Registry(tmp_path, "m1")
    reg.append([
        _e("fg_A", "artifact", "x", "purged", "2026-10-07T10:00:00Z"),
        _e("fg_A", "artifact", "x", "readded", "2026-10-07T11:00:00Z"),
        _e("fg_B", "artifact", "x", "purged", "2026-10-07T09:00:00Z"),
    ])
    assert reg.state_of("artifact", "x") == "purged"


def test_record_readd_cancels_every_live_forget_and_is_a_no_op_otherwise(tmp_path: Path):
    reg = Registry(tmp_path, "m1")
    assert reg.record_readd("artifact", "never", requested_by="ingest") is False
    reg.append([
        _e("fg_A", "artifact", "x", "trashed", "2026-10-07T10:00:00Z"),
        _e("fg_B", "artifact", "x", "trashed", "2026-10-07T10:05:00Z"),
    ])
    assert reg.record_readd("artifact", "x", requested_by="ingest") is True
    assert not reg.is_forgotten("artifact", "x")
    assert reg.record_readd("artifact", "x", requested_by="ingest") is False


def test_any_live_forget_keeps_a_subject_forgotten_whatever_the_clocks_say(tmp_path: Path):
    reg = Registry(tmp_path, "m1")
    reg.append([
        _e("fg_B", "conversation", "c1", "trashed", "2026-10-07T11:00:00Z"),
        _e("fg_A", "conversation", "c1", "trashed", "2026-10-07T10:00:00Z"),
        _e("fg_A", "conversation", "c1", "restored", "2026-10-07T12:00:00Z"),
    ])
    assert reg.state_of("conversation", "c1") == "trashed"
