# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F098 — pkb_privacy_audit must scan document bodies, not just metadata.

The tool is advertised for "auditing the KB for leaks before sharing /
exporting", but its corpus was built solely from the Neo4j artifact row
(filename + summary + keywords). Chunk text — the vast majority of KB
content, and the only place an ingested SSN or PEM key actually lives — was
never read, so an empty `findings` list read as "audited, clean" on a
document whose body was full of credentials.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import config
from app.mcp_tools.temporal import pkb_privacy_audit

pytestmark = pytest.mark.asyncio

_SECRET_BODY = (
    "Wire the payment to the account below.\n"
    "SSN: 123-45-6789\n"
    "-----BEGIN RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
)


def _artifact(**over: Any) -> dict[str, Any]:
    row = {
        "id": "art-1",
        "filename": "notes.md",
        "domain": "personal",
        "summary": "Meeting notes",
        "keywords": "meeting, notes",
        "chunk_ids": json.dumps(["chunk-1", "chunk-2"]),
    }
    row.update(over)
    return row


def _chroma_with(bodies: dict[str, str]) -> Any:
    collection = MagicMock()

    def _get(ids: list[str], **_kw: Any) -> dict[str, Any]:
        present = [i for i in ids if i in bodies]
        return {"ids": present, "documents": [bodies[i] for i in present]}

    collection.get.side_effect = _get
    chroma = MagicMock()
    chroma.get_collection.return_value = collection
    return chroma


async def test_finds_credentials_in_chunk_body() -> None:
    """Metadata is clean; the body is not. The audit must not report clean."""
    with (
        patch("app.mcp_tools.temporal.get_neo4j", return_value=MagicMock()),
        patch("app.db.neo4j.list_artifacts", return_value=[_artifact()]),
        patch("app.deps.get_chroma", return_value=_chroma_with({"chunk-1": _SECRET_BODY})),
    ):
        result = await pkb_privacy_audit()

    patterns = {f["pattern"] for f in result["findings"]}
    assert "ssn_us" in patterns, result
    assert "private_key_pem" in patterns, result
    assert result["chunks_scanned"] == 1, result


async def test_metadata_findings_still_reported() -> None:
    """The pre-existing metadata scan is not lost."""
    row = _artifact(summary="contact bob@example.com about it", chunk_ids="[]")
    with (
        patch("app.mcp_tools.temporal.get_neo4j", return_value=MagicMock()),
        patch("app.db.neo4j.list_artifacts", return_value=[row]),
        patch("app.deps.get_chroma", return_value=_chroma_with({})),
    ):
        result = await pkb_privacy_audit()

    assert {f["pattern"] for f in result["findings"]} == {"email"}
    assert result["chunks_scanned"] == 0


async def test_unreadable_domain_is_reported_not_silently_clean() -> None:
    """A body the audit could not read must never look like a clean body."""
    chroma = MagicMock()
    chroma.get_collection.side_effect = ValueError("no such collection")
    with (
        patch("app.mcp_tools.temporal.get_neo4j", return_value=MagicMock()),
        patch("app.db.neo4j.list_artifacts", return_value=[_artifact()]),
        patch("app.deps.get_chroma", return_value=chroma),
    ):
        result = await pkb_privacy_audit()

    assert result["findings"] == []
    assert result["domains_unscanned"] == ["personal"], result


async def test_scan_is_scoped_to_the_requested_domain() -> None:
    with (
        patch("app.mcp_tools.temporal.get_neo4j", return_value=MagicMock()),
        patch("app.db.neo4j.list_artifacts", return_value=[_artifact(domain="notes")]),
        patch("app.deps.get_chroma", return_value=_chroma_with({"chunk-1": _SECRET_BODY})) as gc,
    ):
        await pkb_privacy_audit(domain="notes")

    collection_names = [
        c.kwargs.get("name") or (c.args[0] if c.args else None)
        for c in gc.return_value.get_collection.call_args_list
    ]
    assert collection_names == [config.collection_name("notes")]
