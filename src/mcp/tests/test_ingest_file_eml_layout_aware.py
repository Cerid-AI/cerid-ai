# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""ingest_file keeps an email's attachments when layout-aware parsing is on."""
from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import pytest

import app.services.ingestion as ing
import config

_SUBJECT = "Quarterly figures"
_MESSAGE_ID = "<figures-0001@example.test>"
_ATTACHMENT_TEXT = "Revenue rose in the third quarter and costs were flat.\n"


def _write_eml(path) -> None:
    msg = EmailMessage()
    msg["From"] = "Sender <sender@example.test>"
    msg["To"] = "Reader <reader@example.test>"
    msg["Subject"] = _SUBJECT
    msg["Message-ID"] = _MESSAGE_ID
    msg["Date"] = "Mon, 07 Sep 2026 09:30:00 +0000"
    msg.set_content("The figures are attached.\n")
    msg.add_attachment(
        _ATTACHMENT_TEXT.encode(),
        maintype="text",
        subtype="plain",
        filename="figures.txt",
    )
    path.write_bytes(msg.as_bytes())


class _Stores:
    """Stands in for the store writes behind ingest_content and the graph."""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    def ingest_content(
        self, content: str, domain: str = "general",
        metadata: dict[str, Any] | None = None, **kwargs: Any,
    ) -> dict[str, Any]:
        self.writes.append({"content": content, "metadata": dict(metadata or {}), **kwargs})
        return {
            "status": "success",
            "artifact_id": f"art-{len(self.writes)}",
            "domain": domain,
            "chunks": 1,
        }

    def write_has_attachment(self, **kwargs: Any) -> bool:
        self.edges.append(kwargs)
        return True


@pytest.fixture
def stores(monkeypatch, tmp_path):
    fake = _Stores()
    monkeypatch.setattr(ing, "ingest_content", fake.ingest_content)
    monkeypatch.setattr(ing.graph, "write_has_attachment", fake.write_has_attachment)
    monkeypatch.setattr(ing, "get_neo4j", lambda: object())
    monkeypatch.setattr(config, "ARCHIVE_PATH", str(tmp_path))
    monkeypatch.setattr(config, "ENABLE_LAYOUT_AWARE_PARSING", True)
    return fake


@pytest.mark.asyncio
async def test_eml_attachment_is_ingested_and_linked_to_the_email(stores, tmp_path):
    eml = tmp_path / "figures.eml"
    _write_eml(eml)

    result = await ing.ingest_file(str(eml), domain="general", skip_metadata=True)

    parent, *children = stores.writes
    # The email itself still goes through the layout-aware chunks.
    assert parent["pre_chunked"]

    assert [c["metadata"]["filename"] for c in children] == ["figures.txt"]
    child = children[0]
    assert child["content"].strip() == _ATTACHMENT_TEXT.strip()
    assert child["metadata"]["parent_artifact_id"] == result["artifact_id"]
    assert child["metadata"]["parent_email_subject"] == _SUBJECT
    assert child["metadata"]["parent_message_id"] == _MESSAGE_ID
    assert child["metadata"]["parent_email_from"]

    assert [(e["parent_id"], e["filename"]) for e in stores.edges] == [
        (result["artifact_id"], "figures.txt")
    ]
    assert [a["filename"] for a in result["attachments_ingested"]] == ["figures.txt"]
