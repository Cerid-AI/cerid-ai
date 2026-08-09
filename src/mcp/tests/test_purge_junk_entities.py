# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``scripts/purge_junk_entities.py`` (Phase 1 item 1.5).

Pure classification / aggregation / batching logic only — no live Neo4j.
The script is loaded via ``importlib`` (matches ``test_beir_purge.py``)
so repo-root ``scripts/`` does not need to be on ``PYTHONPATH``; the
script's own ``sys.path.insert`` makes ``core.*`` / ``app.*`` importable
regardless of how pytest was invoked.
"""
from __future__ import annotations

import importlib.util
import logging
from unittest.mock import MagicMock, patch

import pytest


def _load_script(rel_path: str):
    from tests._helpers import repo_root

    root = repo_root()
    if root is None:
        return None
    script_path = root / rel_path
    if not script_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"_cerid_test_script_{script_path.stem}",
        script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def mod():
    m = _load_script("scripts/purge_junk_entities.py")
    if m is None:
        pytest.skip("scripts/purge_junk_entities.py not reachable from this env")
    return m


# ---------------------------------------------------------------------------
# classify_junk_entity
# ---------------------------------------------------------------------------


class TestClassifyJunkEntityRejects:
    @pytest.mark.parametrize(
        "name,expected_class",
        [
            ("a", "single_char"),
            ("", "single_char"),
            ("x", "single_char"),
            ("library/email.charset.html", "doc_path"),
            ("docs/setup.md", "doc_path"),
            ("version-3-6", "version_token"),
            ("v3.6.1", "version_token"),
            ("ALIASES", "shouty_acronym"),  # 7 chars, all-caps, unknown type
            ("CHARSETS", "shouty_acronym"),  # 8 chars
            ("euc-jp", "codec_alias"),
            ("iso-2022-jp", "codec_alias"),
        ],
    )
    def test_classifies_junk(self, mod, name, expected_class):
        assert mod.classify_junk_entity(name) == expected_class


class TestClassifyJunkEntityKeepers:
    @pytest.mark.parametrize(
        "name",
        [
            "NASA",  # short acronym, plausible org
            "gpt-4",  # hyphenated but not a codec family
            "scikit-learn",  # hyphenated but not a codec family
            "Elon Musk",  # person, has a space
            "BTC/USD",  # has a slash but no doc extension
            "UNESCO",  # 6 chars — at the acronym-length boundary, admitted
            "2024",  # bare number, no separator — admitted (could be a year)
        ],
    )
    def test_keepers_are_not_junk(self, mod, name):
        assert mod.classify_junk_entity(name) is None


# ---------------------------------------------------------------------------
# classify_entity_records + summarize
# ---------------------------------------------------------------------------


def _record(name, entity_type=None, mention_count=0, summary=None, canonical_id=None, rel_count=0):
    return {
        "props": {
            "canonical_id": canonical_id or f"x:{name}",
            "name": name,
            "entity_type": entity_type,
            "mention_count": mention_count,
            "summary": summary,
        },
        "rel_count": rel_count,
    }


class TestClassifyEntityRecords:
    def test_filters_out_keepers(self, mod):
        records = [
            _record("NASA", entity_type="ORG"),
            _record("version-3-6"),
            _record("Elon Musk", entity_type="PERSON"),
        ]
        junk = mod.classify_entity_records(records)
        assert len(junk) == 1
        assert junk[0]["props"]["name"] == "version-3-6"
        assert junk[0]["_junk_class"] == "version_token"

    def test_empty_input_returns_empty(self, mod):
        assert mod.classify_entity_records([]) == []

    def test_preserves_original_fields(self, mod):
        records = [_record("euc-jp", rel_count=3, canonical_id="other:euc-jp")]
        junk = mod.classify_entity_records(records)
        assert junk[0]["rel_count"] == 3
        assert junk[0]["props"]["canonical_id"] == "other:euc-jp"


class TestSummarize:
    def test_counts_and_breakdown(self, mod):
        all_records = [
            _record("NASA"),
            _record("version-3-6", summary=None),
            _record("library/email.charset.html", summary="stale"),
            _record("euc-jp", summary=None),
        ]
        junk = mod.classify_entity_records(all_records)
        summary = mod.summarize(all_records, junk)

        assert summary["total_entities"] == 4
        assert summary["junk_count"] == 3
        assert summary["junk_share"] == pytest.approx(0.75)
        assert summary["by_class"] == {
            "version_token": 1,
            "doc_path": 1,
            "codec_alias": 1,
        }
        # version-3-6 and euc-jp both have summary=None; the doc-path one
        # has a (stale) summary — only 2 of the 3 junk nodes are NULL.
        assert summary["summary_null_count"] == 2

    def test_empty_total_gives_zero_share(self, mod):
        summary = mod.summarize([], [])
        assert summary["total_entities"] == 0
        assert summary["junk_count"] == 0
        assert summary["junk_share"] == 0.0

    def test_all_keepers_gives_zero_junk(self, mod):
        all_records = [_record("NASA"), _record("gpt-4")]
        junk = mod.classify_entity_records(all_records)
        summary = mod.summarize(all_records, junk)
        assert summary["junk_count"] == 0
        assert summary["by_class"] == {}


# ---------------------------------------------------------------------------
# is_junk_share_unsafe — safety rail
# ---------------------------------------------------------------------------


class TestJunkShareSafetyRail:
    def test_below_threshold_is_safe(self, mod):
        assert mod.is_junk_share_unsafe(0.59) is False

    def test_at_threshold_is_safe(self, mod):
        # "exceeds" is strict — exactly at the threshold is still allowed.
        assert mod.is_junk_share_unsafe(0.60) is False

    def test_above_threshold_is_unsafe(self, mod):
        assert mod.is_junk_share_unsafe(0.61) is True

    def test_all_junk_is_unsafe(self, mod):
        assert mod.is_junk_share_unsafe(1.0) is True


# ---------------------------------------------------------------------------
# delete_junk_entities — batching, mocked driver
# ---------------------------------------------------------------------------


class TestDeleteJunkEntities:
    def _mock_driver(self, deleted_per_batch):
        """Build a fake driver whose session().run().single() cycles through
        ``deleted_per_batch`` — one entry consumed per UNWIND batch."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.side_effect = [
            MagicMock(single=MagicMock(return_value={"n": n})) for n in deleted_per_batch
        ]
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        return mock_driver, mock_session

    def test_single_batch_under_size(self, mod):
        driver, session = self._mock_driver([3])
        deleted = mod.delete_junk_entities(driver, ["a", "b", "c"], batch_size=200)
        assert deleted == 3
        assert session.run.call_count == 1

    def test_splits_into_multiple_batches(self, mod):
        ids = [f"id-{i}" for i in range(5)]
        driver, session = self._mock_driver([2, 2, 1])
        deleted = mod.delete_junk_entities(driver, ids, batch_size=2)
        assert deleted == 5
        assert session.run.call_count == 3
        # Verify the UNWIND params were chunked correctly.
        called_batches = [call.kwargs["ids"] for call in session.run.call_args_list]
        assert called_batches == [["id-0", "id-1"], ["id-2", "id-3"], ["id-4"]]

    def test_empty_ids_makes_no_calls(self, mod):
        driver, session = self._mock_driver([])
        deleted = mod.delete_junk_entities(driver, [], batch_size=200)
        assert deleted == 0
        session.run.assert_not_called()

    def test_uses_named_constant_default(self, mod):
        assert mod._DELETE_BATCH_SIZE == 200


# ---------------------------------------------------------------------------
# write_backup — real filesystem (tmp_path), no Neo4j
# ---------------------------------------------------------------------------


class TestWriteBackup:
    def test_writes_one_jsonl_line_per_record(self, mod, tmp_path):
        import json

        junk = [
            {
                "props": {"canonical_id": "x:a", "name": "a", "summary": None},
                "rel_count": 0,
                "_junk_class": "single_char",
            },
            {
                "props": {"canonical_id": "x:euc-jp", "name": "euc-jp", "summary": None},
                "rel_count": 2,
                "_junk_class": "codec_alias",
            },
        ]
        out_path = tmp_path / "nested" / "backup.jsonl"
        mod.write_backup(junk, out_path)

        assert out_path.exists()
        lines = out_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["props"]["name"] == "a"
        assert first["junk_class"] == "single_char"
        assert first["rel_count"] == 0

    def test_empty_junk_creates_empty_file(self, mod, tmp_path):
        out_path = tmp_path / "backup.jsonl"
        mod.write_backup([], out_path)
        assert out_path.exists()
        assert out_path.read_text(encoding="utf-8") == ""


class TestBackupPath:
    def test_shape(self, mod):
        path = mod._backup_path()
        assert path.parent.name == "out"
        assert path.parent.parent.name == "scripts"
        assert path.name.startswith("junk_entities_backup_")
        assert path.name.endswith(".jsonl")


# ---------------------------------------------------------------------------
# _get_neo4j_driver — no-password short circuit (must not touch the network)
# ---------------------------------------------------------------------------


class TestGetNeo4jDriver:
    def test_missing_password_returns_none(self, mod, monkeypatch):
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
        assert mod._get_neo4j_driver() is None


# ---------------------------------------------------------------------------
# main() orchestration — fully mocked driver/IO, never touches live Neo4j
# ---------------------------------------------------------------------------


class TestMainDryRun:
    def test_dry_run_does_not_write_or_delete(self, mod, caplog):
        records = [_record("NASA"), _record("version-3-6")]
        with (
            patch.object(mod, "_load_dotenv_into_environ"),
            patch.object(mod, "_get_neo4j_driver", return_value=MagicMock()),
            patch.object(mod, "_fetch_entity_records", return_value=records),
            patch.object(mod, "write_backup") as mock_write,
            patch.object(mod, "delete_junk_entities") as mock_delete,
            caplog.at_level(logging.INFO, logger="purge-junk-entities"),
        ):
            rc = mod.main([])

        assert rc == mod._EXIT_OK
        mock_write.assert_not_called()
        mock_delete.assert_not_called()
        assert "DRY-RUN" in " ".join(caplog.messages)


class TestMainApply:
    def test_apply_writes_backup_and_deletes(self, mod, tmp_path):
        # 2 of 5 = 40% junk — under the 60% safety threshold.
        records = [
            _record("NASA"),
            _record("gpt-4"),
            _record("Elon Musk"),
            _record("version-3-6"),
            _record("euc-jp"),
        ]
        fake_backup_path = tmp_path / "backup.jsonl"
        with (
            patch.object(mod, "_load_dotenv_into_environ"),
            patch.object(mod, "_get_neo4j_driver", return_value=MagicMock()),
            patch.object(mod, "_fetch_entity_records", return_value=records),
            patch.object(mod, "_backup_path", return_value=fake_backup_path),
            patch.object(mod, "write_backup") as mock_write,
            patch.object(mod, "delete_junk_entities", return_value=2) as mock_delete,
        ):
            rc = mod.main(["--apply"])

        assert rc == mod._EXIT_OK
        mock_write.assert_called_once()
        written_junk = mock_write.call_args.args[0]
        assert {r["props"]["name"] for r in written_junk} == {"version-3-6", "euc-jp"}
        mock_delete.assert_called_once()
        deleted_ids = mock_delete.call_args.args[1]
        assert set(deleted_ids) == {"x:version-3-6", "x:euc-jp"}

    def test_apply_refuses_when_junk_share_unsafe(self, mod):
        # 3 of 4 = 75% junk — over the 60% safety threshold.
        records = [
            _record("version-3-6"),
            _record("euc-jp"),
            _record("ALIASES"),
            _record("NASA"),
        ]
        with (
            patch.object(mod, "_load_dotenv_into_environ"),
            patch.object(mod, "_get_neo4j_driver", return_value=MagicMock()),
            patch.object(mod, "_fetch_entity_records", return_value=records),
            patch.object(mod, "write_backup") as mock_write,
            patch.object(mod, "delete_junk_entities") as mock_delete,
        ):
            rc = mod.main(["--apply"])

        assert rc == mod._EXIT_UNSAFE_JUNK_SHARE
        mock_write.assert_not_called()
        mock_delete.assert_not_called()

    def test_apply_with_no_junk_is_a_noop(self, mod):
        records = [_record("NASA"), _record("gpt-4")]
        with (
            patch.object(mod, "_load_dotenv_into_environ"),
            patch.object(mod, "_get_neo4j_driver", return_value=MagicMock()),
            patch.object(mod, "_fetch_entity_records", return_value=records),
            patch.object(mod, "write_backup") as mock_write,
            patch.object(mod, "delete_junk_entities") as mock_delete,
        ):
            rc = mod.main(["--apply"])

        assert rc == mod._EXIT_OK
        mock_write.assert_not_called()
        mock_delete.assert_not_called()


class TestMainNeo4jUnavailable:
    def test_returns_error_exit_code(self, mod):
        with (
            patch.object(mod, "_load_dotenv_into_environ"),
            patch.object(mod, "_get_neo4j_driver", return_value=None),
        ):
            rc = mod.main([])
        assert rc == mod._EXIT_NEO4J_UNAVAILABLE
