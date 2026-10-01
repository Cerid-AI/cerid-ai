# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A file whose ingest failed is retried by the next folder scan."""
from __future__ import annotations

import pytest

from app.services import folder_scanner


class _DictRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def incr(self, key: str) -> None:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)

    def smembers(self, key: str) -> set[str]:
        return set()


@pytest.fixture
def scanner(monkeypatch):
    """Scanner wired to an in-memory store and an ingest that fails once."""
    redis = _DictRedis()
    calls: list[str] = []

    async def ingest_file(**kwargs):
        calls.append(kwargs["file_path"])
        if len(calls) == 1:
            raise RuntimeError("vector store unavailable")
        return {"status": "success", "artifact_id": "art-1", "quality_score": 0.9}

    monkeypatch.setattr(folder_scanner, "get_redis", lambda: redis)
    monkeypatch.setattr(folder_scanner, "ingest_file", ingest_file)
    return calls


async def _scan(root) -> list[folder_scanner.ScanResult]:
    return [r async for r in folder_scanner.scan_folder(str(root), extensions={".md"})]


@pytest.mark.asyncio
async def test_file_that_errored_is_ingested_on_the_next_scan(tmp_path, scanner):
    (tmp_path / "note.md").write_text("# Note\n\nSome content worth keeping.\n")

    first = await _scan(tmp_path)
    second = await _scan(tmp_path)
    third = await _scan(tmp_path)

    assert [r.status for r in first] == ["error"]
    assert [r.status for r in second] == ["ingested"]
    assert second[0].artifact_id == "art-1"
    assert len(scanner) == 2
    # Once it has been ingested the file is a duplicate again.
    assert [r.status for r in third] == ["duplicate"]
