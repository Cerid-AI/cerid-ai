# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A successful delete writes a tombstone before the other stores are fanned out."""

import json
from unittest.mock import patch

from app.services import content_lifecycle


def test_remove_content_records_the_artifact_and_each_attachment(tmp_path):
    info = {
        "deleted": True,
        "chunk_ids": ["c1", "c2"],
        "domain": "general",
        "filename": "note.md",
        "attachment_ids": ["child-1"],
    }
    with (
        patch("app.db.neo4j.artifacts.delete_artifact", return_value=info),
        patch("app.services.content_lifecycle._fan_out_removal", return_value={"chroma": 2}),
        patch("app.services.content_lifecycle.invalidate_caches"),
    ):
        result = content_lifecycle.remove_content(
            "art-parent", neo4j=object(), chroma=object(), redis=object(),
        )

    assert result.found is True
    rows = [
        json.loads(line)
        for line in (tmp_path / "tombstones.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["artifact_id"] for row in rows] == ["art-parent", "child-1"]
    assert rows[0]["chunk_ids"] == ["c1", "c2"]
    assert rows[0]["domain"] == "general"
    assert rows[0]["filename"] == "note.md"
    assert rows[1]["chunk_ids"] == []
    assert rows[1]["domain"] == "general"


def test_a_missing_artifact_writes_no_tombstone(tmp_path):
    with patch(
        "app.db.neo4j.artifacts.delete_artifact",
        return_value={"deleted": False},
    ):
        result = content_lifecycle.remove_content(
            "missing", neo4j=object(), chroma=object(), redis=object(),
        )

    assert result.found is False
    assert not (tmp_path / "tombstones.jsonl").exists()
