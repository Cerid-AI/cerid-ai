# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the email parser + chunker (Workstream E Phase 2b.4)."""

from __future__ import annotations

from textwrap import dedent

import pytest

from core.ingest.chunkers import chunk_elements
from core.ingest.chunkers.email_strategy import (
    email_body_strategy,
    email_header_strategy,
    email_thread_edge_strategy,
)
from core.ingest.parsers.email_parser import parse_email, parse_email_string

# ---------------------------------------------------------------------------
# Synthetic .eml fixtures — public-corpus-style minimal samples
# ---------------------------------------------------------------------------


SIMPLE_EML = dedent("""\
    From: alice@example.com
    To: bob@example.com
    Subject: Project kickoff
    Date: Thu, 28 Apr 2026 10:00:00 -0500
    Message-ID: <abc-123@example.com>
    MIME-Version: 1.0
    Content-Type: text/plain

    Hi Bob — let's discuss the project on Tuesday.

    Best,
    Alice
""")


REPLY_EML = dedent("""\
    From: bob@example.com
    To: alice@example.com
    Subject: Re: Project kickoff
    Date: Thu, 28 Apr 2026 11:00:00 -0500
    Message-ID: <reply-456@example.com>
    In-Reply-To: <abc-123@example.com>
    References: <abc-123@example.com>
    MIME-Version: 1.0
    Content-Type: text/plain

    Sounds good — Tuesday at 2pm works for me.

    On Apr 28, 2026, Alice wrote:
    > Hi Bob — let's discuss the project on Tuesday.
    >
    > Best,
    > Alice
""")


MULTI_RECIPIENT_EML = dedent("""\
    From: alice@example.com
    To: bob@example.com, carol@example.com
    Cc: dave@example.com
    Subject: Onboarding summary
    Date: Thu, 28 Apr 2026 12:00:00 -0500
    Message-ID: <multi-789@example.com>
    Content-Type: text/plain

    Welcome Carol! Here's the onboarding summary.
""")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_email_emits_header_and_body(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(SIMPLE_EML)
    types = [el["element_type"] for el in elements]
    assert "EmailHeader" in types
    assert "EmailBody" in types
    # No In-Reply-To → no thread edge
    assert "EmailThreadEdge" not in types


def test_parse_email_header_metadata_preserves_structured_fields(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(SIMPLE_EML)
    header = next(el for el in elements if el["element_type"] == "EmailHeader")
    md = header["metadata"]
    assert md["from"] == "alice@example.com"
    assert md["to"] == ["bob@example.com"]
    assert md["subject"] == "Project kickoff"
    assert md["message_id"] == "<abc-123@example.com>"
    # No prior thread → thread_id falls back to this message's own ID
    assert md["thread_id"] == "<abc-123@example.com>"


def test_parse_email_header_text_renders_searchable_block(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(SIMPLE_EML)
    header = next(el for el in elements if el["element_type"] == "EmailHeader")
    # The header element's text is the embed-ready block
    assert "from: alice@example.com" in header["text"]
    assert "subject: Project kickoff" in header["text"]


def test_parse_email_strips_quoted_replies(monkeypatch):
    """quotequail.unwrap drops the '> ...' previous message from the body."""
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(REPLY_EML)
    body = next(el for el in elements if el["element_type"] == "EmailBody")
    assert "Tuesday at 2pm works" in body["text"]
    # The quoted text from the prior message should be stripped
    assert "Hi Bob — let's discuss" not in body["text"]


def test_parse_email_thread_edge_emitted_on_reply(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(REPLY_EML)
    edges = [el for el in elements if el["element_type"] == "EmailThreadEdge"]
    assert len(edges) == 1
    md = edges[0]["metadata"]
    assert md["message_id"] == "<reply-456@example.com>"
    assert md["in_reply_to"] == "<abc-123@example.com>"
    assert md["thread_id"] == "<abc-123@example.com>"  # references[0] = root


def test_parse_email_resolves_thread_via_references(monkeypatch):
    """When References has multiple ids, the FIRST (oldest) is the thread root."""
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    eml = dedent("""\
        From: alice@example.com
        To: bob@example.com
        Subject: Re: thread root
        Date: Thu, 28 Apr 2026 12:00:00 -0500
        Message-ID: <leaf-999@example.com>
        In-Reply-To: <middle-456@example.com>
        References: <root-001@example.com> <middle-456@example.com>
        Content-Type: text/plain

        Latest reply.
    """)
    elements = parse_email_string(eml)
    header = next(el for el in elements if el["element_type"] == "EmailHeader")
    assert header["metadata"]["thread_id"] == "<root-001@example.com>"


def test_parse_email_multi_recipient_normalises_to_address_list(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(MULTI_RECIPIENT_EML)
    header = next(el for el in elements if el["element_type"] == "EmailHeader")
    md = header["metadata"]
    # mail-parser returns [(name, addr)] tuples; we flatten to bare addresses
    assert md["to"] == ["bob@example.com", "carol@example.com"]
    assert md["cc"] == ["dave@example.com"]


def test_parse_email_redacts_addresses_when_anonymize_on(monkeypatch):
    """ANONYMIZE_EMAIL_HEADERS=true masks the local-part in rendered text."""
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", True)
    elements = parse_email_string(SIMPLE_EML)
    header = next(el for el in elements if el["element_type"] == "EmailHeader")
    # Domain is preserved (the email domain itself is rarely PII)
    assert "[redacted]@example.com" in header["text"]
    assert "alice@example.com" not in header["text"]
    # The structured metadata still has the raw address — privacy-aware
    # retrieval can re-redact per query context.
    assert header["metadata"]["from"] == "alice@example.com"


def test_parse_email_handles_empty_string():
    assert parse_email_string("") == []
    assert parse_email_string("   \n  ") == []


def test_parse_email_skips_empty_body():
    """An email with headers but an empty body produces no EmailBody element."""
    eml = dedent("""\
        From: alice@example.com
        To: bob@example.com
        Subject: ping
        Message-ID: <abc@example.com>
        Content-Type: text/plain

    """)
    elements = parse_email_string(eml)
    types = [el["element_type"] for el in elements]
    assert "EmailHeader" in types
    assert "EmailBody" not in types


def test_parse_email_file_round_trip(tmp_path):
    p = tmp_path / "test.eml"
    p.write_text(SIMPLE_EML, encoding="utf-8")
    elements = parse_email(p)
    assert len(elements) >= 2
    assert any(el["element_type"] == "EmailHeader" for el in elements)


def test_parse_email_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_email(tmp_path / "missing.eml")


def test_parse_email_handles_malformed_input_gracefully():
    """Garbage input produces an empty parse, not an exception."""
    # mail-parser will accept this but produce minimal metadata
    elements = parse_email_string("not an email at all just some text\n")
    # Either empty (unparseable) or just an EmailHeader with empty fields —
    # the contract is "doesn't raise"; assert that property
    assert isinstance(elements, list)


# ---------------------------------------------------------------------------
# Chunker strategies
# ---------------------------------------------------------------------------


def test_email_header_strategy_passes_through():
    el = {
        "text": "from: alice@x.com\nsubject: Hi",
        "element_type": "EmailHeader",
        "metadata": {"from": "alice@x.com", "subject": "Hi", "thread_id": "<a@x>"},
    }
    chunks = email_header_strategy(el)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "from: alice@x.com\nsubject: Hi"
    assert chunks[0]["metadata"]["element_type"] == "EmailHeader"
    assert chunks[0]["metadata"]["thread_id"] == "<a@x>"


def test_email_body_strategy_prepends_thread_breadcrumb():
    el = {
        "text": "Body text here.",
        "element_type": "EmailBody",
        "metadata": {"thread_id": "<root@x>", "message_id": "<m@x>"},
    }
    chunks = email_body_strategy(el)
    assert chunks[0]["text"].startswith("Thread <root@x>")
    assert "Body text here." in chunks[0]["text"]


def test_email_body_strategy_skips_empty_body():
    el = {"text": "", "element_type": "EmailBody", "metadata": {"thread_id": "x"}}
    assert email_body_strategy(el) == []


def test_email_body_strategy_splits_oversized(monkeypatch):
    import config
    monkeypatch.setattr(config, "PARENT_CHUNK_TOKENS", 30)
    el = {
        "text": "very long body sentence " * 60,
        "element_type": "EmailBody",
        "metadata": {"thread_id": "<root@x>", "message_id": "<m@x>"},
    }
    chunks = email_body_strategy(el)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["text"].startswith("Thread <root@x>")
    indices = [c["metadata"].get("body_chunk_idx") for c in chunks]
    assert indices == list(range(len(chunks)))


def test_email_thread_edge_strategy_emits_metadata_only_chunk():
    el = {
        "text": "",
        "element_type": "EmailThreadEdge",
        "metadata": {
            "thread_id": "<root@x>",
            "message_id": "<m@x>",
            "in_reply_to": "<prev@x>",
        },
    }
    chunks = email_thread_edge_strategy(el)
    assert len(chunks) == 1
    assert chunks[0]["text"] == ""
    md = chunks[0]["metadata"]
    assert md["element_type"] == "EmailThreadEdge"
    assert md["in_reply_to"] == "<prev@x>"
    assert md["thread_id"] == "<root@x>"


# ---------------------------------------------------------------------------
# End-to-end dispatch
# ---------------------------------------------------------------------------


def test_chunk_elements_dispatches_email_strategies(monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    elements = parse_email_string(REPLY_EML)
    chunks = chunk_elements(elements)

    types = {c["metadata"]["element_type"] for c in chunks}
    assert "EmailHeader" in types
    assert "EmailBody" in types
    assert "EmailThreadEdge" in types

    # Header chunk carries searchable text
    header_chunk = next(c for c in chunks if c["metadata"]["element_type"] == "EmailHeader")
    assert "from: bob@example.com" in header_chunk["text"]

    # Body chunk has the breadcrumb + reply-stripped body
    body_chunk = next(c for c in chunks if c["metadata"]["element_type"] == "EmailBody")
    assert body_chunk["text"].startswith("Thread <abc-123@example.com>")
    assert "Tuesday at 2pm" in body_chunk["text"]
    assert "Hi Bob — let's discuss" not in body_chunk["text"]
