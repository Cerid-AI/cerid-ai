# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration test for vault-aware folder scanning (RAG Cycle C2.3 Phase B).

Verifies that ``scan_vault`` correctly routes files based on the vault
profile:

* ``templates/`` and ``skip_folders`` are skipped entirely
* ``mocs/`` files get ``sub_category="moc"``
* ``daily/`` files get ``sub_category="daily"``
* ``attachments/`` PDFs are routed through ``ingest_file`` (binary corpus)
* Regular markdown files get default classification

The test patches ``get_redis`` and ``ingest_file`` so it never hits the
real Redis / ingestion pipeline — we just want to assert the
classification + dispatch is wired up correctly.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import folder_scanner


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Materialise a minimal vault layout for scan_vault to walk."""
    (tmp_path / "mocs").mkdir()
    (tmp_path / "mocs" / "index.md").write_text(
        "# MOC index\nThis is the map of content.\n",
        encoding="utf-8",
    )
    (tmp_path / "daily").mkdir()
    (tmp_path / "daily" / "2026-05-11.md").write_text(
        "# 2026-05-11\nDaily journal entry.\n",
        encoding="utf-8",
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "skel.md").write_text(
        "# {{title}}\nTemplate body.\n",
        encoding="utf-8",
    )
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "diagram.pdf").write_bytes(b"%PDF-1.4\n%fake pdf\n")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "idea.md").write_text(
        "# Idea\nA regular note.\n",
        encoding="utf-8",
    )

    (tmp_path / ".cerid-vault.yaml").write_text(
        "mocs_folders:\n  - mocs\n"
        "daily_folders:\n  - daily\n"
        "templates_folders:\n  - templates\n"
        "attachments_folders:\n  - attachments\n"
        "default_domain: general\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def patched_scanner(monkeypatch):
    """Replace ``get_redis`` and ``ingest_file`` with capturing mocks.

    Returns ``(ingest_calls, redis_mock)`` so tests can assert on the
    exact arguments passed to ``ingest_file``.
    """
    redis_mock = MagicMock()
    redis_mock.get.return_value = None  # no dedup hits
    monkeypatch.setattr(folder_scanner, "get_redis", lambda: redis_mock)

    ingest_calls: list[dict] = []

    async def fake_ingest_file(**kwargs):
        ingest_calls.append(kwargs)
        return {
            "artifact_id": f"art-{len(ingest_calls)}",
            "quality_score": 0.9,
            "duplicate": False,
        }

    monkeypatch.setattr(folder_scanner, "ingest_file", fake_ingest_file)
    return ingest_calls, redis_mock


@pytest.mark.asyncio
async def test_scan_vault_routes_subfolders_correctly(vault_dir, patched_scanner):
    ingest_calls, _redis = patched_scanner

    results = []
    async for result in folder_scanner.scan_vault(str(vault_dir), None):
        results.append(result)

    # Bucket results by status
    by_status: dict[str, list] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    # Templates must be skipped (never ingested).
    template_results = [r for r in by_status.get("skipped", []) if "templates/" in r.path]
    assert template_results, "templates folder must produce skipped results"

    # The template file should NOT appear in ingest_file calls.
    ingested_paths = {call["file_path"] for call in ingest_calls}
    assert not any("templates/" in p for p in ingested_paths)

    # MOC, daily, attachment, regular all reach ingest_file.
    assert any("mocs/" in p for p in ingested_paths), "mocs file should ingest"
    assert any("daily/" in p for p in ingested_paths), "daily file should ingest"
    assert any("attachments/" in p for p in ingested_paths), "attachment should ingest"
    assert any("notes/" in p for p in ingested_paths), "regular note should ingest"


@pytest.mark.asyncio
async def test_scan_vault_assigns_correct_sub_categories(vault_dir, patched_scanner):
    ingest_calls, _redis = patched_scanner

    async for _ in folder_scanner.scan_vault(str(vault_dir), None):
        pass

    # Build {basename: sub_category} from the captured ingest_file calls.
    by_name = {Path(call["file_path"]).name: call.get("sub_category", "") for call in ingest_calls}

    assert by_name.get("index.md") == "moc"
    assert by_name.get("2026-05-11.md") == "daily"
    # Regular notes get the empty fallback sub_category (no taxonomy match).
    assert by_name.get("idea.md") == ""


@pytest.mark.asyncio
async def test_scan_vault_uses_default_domain(vault_dir, patched_scanner):
    ingest_calls, _redis = patched_scanner

    async for _ in folder_scanner.scan_vault(str(vault_dir), None):
        pass

    # default_domain from YAML is "general"; the regular note has no
    # taxonomy-detected domain, so it should fall back to "general".
    notes_call = next(c for c in ingest_calls if "notes/idea.md" in c["file_path"])
    assert notes_call["domain"] == "general"


@pytest.mark.asyncio
async def test_scan_vault_ui_config_when_yaml_missing(tmp_path, patched_scanner):
    # Same layout but no .cerid-vault.yaml — UI config must be honored.
    (tmp_path / "my-mocs").mkdir()
    (tmp_path / "my-mocs" / "index.md").write_text("# MOC\n", encoding="utf-8")
    (tmp_path / "skip-me").mkdir()
    (tmp_path / "skip-me" / "internal.md").write_text("# Internal\n", encoding="utf-8")

    ui_config = {
        "mocs_folders": ["my-mocs"],
        "skip_folders": ["skip-me"],
        "default_domain": "research",
    }

    ingest_calls, _redis = patched_scanner

    async for _ in folder_scanner.scan_vault(str(tmp_path), ui_config):
        pass

    ingested_paths = {call["file_path"] for call in ingest_calls}
    assert any("my-mocs/index.md" in p for p in ingested_paths)
    assert not any("skip-me/" in p for p in ingested_paths)

    moc_call = next(c for c in ingest_calls if "my-mocs/index.md" in c["file_path"])
    assert moc_call["sub_category"] == "moc"
    assert moc_call["domain"] == "research"


@pytest.mark.asyncio
async def test_scan_vault_yaml_overrides_ui(tmp_path, patched_scanner):
    # YAML wins on key conflicts.
    (tmp_path / "yaml-mocs").mkdir()
    (tmp_path / "yaml-mocs" / "x.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "ui-mocs").mkdir()
    (tmp_path / "ui-mocs" / "y.md").write_text("# y\n", encoding="utf-8")
    (tmp_path / ".cerid-vault.yaml").write_text(
        "mocs_folders:\n  - yaml-mocs\n",
        encoding="utf-8",
    )

    ui_config = {"mocs_folders": ["ui-mocs"]}

    ingest_calls, _redis = patched_scanner

    async for _ in folder_scanner.scan_vault(str(tmp_path), ui_config):
        pass

    by_name = {Path(call["file_path"]).name: call.get("sub_category", "") for call in ingest_calls}
    # x.md (in yaml-mocs) gets "moc" because YAML wins
    assert by_name.get("x.md") == "moc"
    # y.md (in ui-mocs) is REGULAR because YAML's mocs_folders=[yaml-mocs]
    # does NOT include ui-mocs; defaults are bypassed entirely for that key.
    assert by_name.get("y.md") == ""
