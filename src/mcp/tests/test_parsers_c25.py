# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""RAG Cycle C2.5 — parser improvement coverage.

Sub-task 1: ``parse_mbox`` surfaces truncation as structured fields.
Sub-task 2: ``parse_pptx`` extracts slide text; ``parse_ppt`` raises 422.
Sub-task 3: ``parse_msg`` is registered + dispatches; basic extraction.

Heavy parsers are mocked when their backing library isn't on the host
(python-pptx) so the test suite runs even without the
optional deps installed. The mocks substitute the modules via
``sys.modules`` so the parsers' lazy imports pick them up.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.parsers.email import parse_mbox, parse_msg
from app.parsers.office import parse_ppt, parse_pptx
from app.parsers.registry import PARSER_REGISTRY, parse_file

# ---------------------------------------------------------------------------
# Sub-task 1: parse_mbox truncation surfacing
# ---------------------------------------------------------------------------


def _write_mbox(tmp_path, count: int):
    """Build a real .mbox with ``count`` simple messages."""
    import mailbox

    path = tmp_path / "test.mbox"
    mbox = mailbox.mbox(str(path))
    for i in range(count):
        msg = mailbox.mboxMessage()
        msg["From"] = f"sender{i}@example.com"
        msg["To"] = "rcv@example.com"
        msg["Subject"] = f"Message {i}"
        msg["Date"] = "Mon, 1 Jan 2026 00:00:00 +0000"
        msg.set_payload(f"Body of message {i}")
        mbox.add(msg)
    mbox.flush()
    mbox.close()
    return path


def test_mbox_truncation_fields_present_when_under_cap(tmp_path, monkeypatch):
    """A small mbox produces truncated=False and total = page_count."""
    monkeypatch.setattr("config.MBOX_MESSAGE_CAP", 100)
    path = _write_mbox(tmp_path, count=3)
    result = parse_mbox(str(path))

    assert result["mbox_truncated"] is False
    assert result["mbox_total_messages"] == 3
    assert result["mbox_message_cap"] == 100
    assert result["page_count"] == 3
    # No truncation marker in body text
    assert "more messages truncated" not in result["text"]


def test_mbox_truncation_fields_present_when_over_cap(tmp_path, monkeypatch):
    """Above the cap: truncated=True, total reflects the real count."""
    monkeypatch.setattr("config.MBOX_MESSAGE_CAP", 2)
    path = _write_mbox(tmp_path, count=5)
    result = parse_mbox(str(path))

    assert result["mbox_truncated"] is True
    assert result["mbox_total_messages"] == 5
    assert result["mbox_message_cap"] == 2
    # Body text records the truncation count for the chunker
    assert "3 more messages truncated" in result["text"]


def test_mbox_message_cap_env_var_honored(tmp_path, monkeypatch):
    """``config.MBOX_MESSAGE_CAP`` is read per-call (not import-time)."""
    monkeypatch.setattr("config.MBOX_MESSAGE_CAP", 1)
    path = _write_mbox(tmp_path, count=3)
    result = parse_mbox(str(path))
    assert result["mbox_message_cap"] == 1
    assert result["mbox_truncated"] is True


# ---------------------------------------------------------------------------
# Sub-task 2: parse_pptx + parse_ppt
# ---------------------------------------------------------------------------


def _make_pptx_module():
    """Build a fake ``pptx`` module shaped like python-pptx's API."""
    # Two slides:
    #   Slide 1: two paragraphs (run text concatenated per paragraph)
    #   Slide 2: one paragraph + speaker notes
    def _para(text: str):
        run = MagicMock()
        run.text = text
        para = MagicMock()
        para.runs = [run]
        return para

    def _shape(paragraphs):
        shape = MagicMock()
        shape.has_text_frame = True
        shape.text_frame.paragraphs = paragraphs
        return shape

    slide1 = MagicMock()
    slide1.has_notes_slide = False
    slide1.shapes = [_shape([_para("Slide 1 title"), _para("Slide 1 body")])]

    slide2 = MagicMock()
    slide2.has_notes_slide = True
    slide2.notes_slide.notes_text_frame.text = "Speaker note A"
    slide2.shapes = [_shape([_para("Slide 2 content")])]

    prs = MagicMock()
    prs.slides = [slide1, slide2]

    fake_module = SimpleNamespace(Presentation=MagicMock(return_value=prs))
    return fake_module, prs


def _build_minimal_zip(path):
    """A tiny but real zipfile so ``assert_safe_zip`` doesn't blow up."""
    import zipfile

    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("placeholder.txt", "stub")


def test_parse_pptx_extracts_slide_text(tmp_path, monkeypatch):
    path = tmp_path / "deck.pptx"
    _build_minimal_zip(path)

    fake_pptx, _ = _make_pptx_module()
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    result = parse_pptx(str(path))
    assert result["file_type"] == "pptx"
    assert result["slide_count"] == 2
    assert result["page_count"] == 2
    assert "--- Slide 1 ---" in result["text"]
    assert "Slide 1 title" in result["text"]
    assert "Slide 1 body" in result["text"]
    assert "--- Slide 2 ---" in result["text"]
    assert "Slide 2 content" in result["text"]
    assert "[Notes] Speaker note A" in result["text"]


def test_parse_pptx_registered():
    assert ".pptx" in PARSER_REGISTRY


def test_parse_pptx_corrupted_raises(tmp_path, monkeypatch):
    path = tmp_path / "bad.pptx"
    _build_minimal_zip(path)

    fake_pptx = SimpleNamespace(Presentation=MagicMock(side_effect=Exception("bad pptx")))
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    with pytest.raises(ValueError, match="corrupted"):
        parse_pptx(str(path))


def test_parse_pptx_dispatches_through_registry(tmp_path, monkeypatch):
    path = tmp_path / "deck.pptx"
    _build_minimal_zip(path)

    fake_pptx, _ = _make_pptx_module()
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    result = parse_file(str(path))
    assert result["file_type"] == "pptx"
    assert result["slide_count"] == 2


def test_parse_ppt_returns_422(tmp_path):
    """Legacy .ppt — caller gets a 422 with conversion guidance, not a crash."""
    path = tmp_path / "old.ppt"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)  # CFB header stub

    with pytest.raises(HTTPException) as exc:
        parse_ppt(str(path))
    assert exc.value.status_code == 422
    assert "convert" in str(exc.value.detail).lower()


def test_parse_ppt_registered():
    assert ".ppt" in PARSER_REGISTRY


# ---------------------------------------------------------------------------
# Sub-task 3: parse_msg
# ---------------------------------------------------------------------------


def _fake_msg(
    *,
    sender="alice@example.com",
    to="bob@example.com",
    subject="Hello from Outlook",
    body="Body text from .msg",
    attachments=None,
):
    """A decoded MsgMessage, as app.parsers.msg_reader.open_msg would return.

    These tests cover parse_msg's dict-shaping and anonymisation, so they stub
    the reader at its boundary. The DECODING is tested for real, against real
    MS-OXMSG bytes, in test_msg_reader.py — which is what the old
    `sys.modules["extract_msg"] = MagicMock()` approach never did for the
    library it replaced.
    """
    from app.parsers.msg_reader import MsgMessage

    return MsgMessage(
        subject=subject,
        sender=sender,
        to=to,
        cc=None,
        date="Mon, 1 Jan 2026 00:00:00 +0000",
        message_id="<msg-1@example.com>",
        body=body,
        html_body=None,
        attachments=attachments or [],
    )


def test_parse_msg_extracts_headers_and_body(tmp_path, monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    path = tmp_path / "outlook.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)  # CFB stub

    monkeypatch.setattr("app.parsers.msg_reader.open_msg", lambda _p: _fake_msg())

    result = parse_msg(str(path))
    assert result["file_type"] == "msg"
    assert result["subject"] == "Hello from Outlook"
    assert result["from"] == "alice@example.com"
    assert result["message_id"] == "<msg-1@example.com>"
    assert "Body text from .msg" in result["text"]
    assert "From: alice@example.com" in result["text"]


def test_parse_msg_registered():
    assert ".msg" in PARSER_REGISTRY


def test_parse_msg_anonymizes_headers_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", True)
    path = tmp_path / "outlook.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr("app.parsers.msg_reader.open_msg", lambda _p: _fake_msg())

    result = parse_msg(str(path))
    assert "[redacted]@example.com" in result["text"]
    assert "alice@example.com" not in result["text"]


def test_parse_msg_surfaces_attachments(tmp_path, monkeypatch):
    """Embedded attachments become AttachmentBlob entries for C2.4 recursion."""
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    path = tmp_path / "outlook.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    from app.parsers.msg_reader import MsgAttachment

    att = MsgAttachment(filename="report.pdf", data=b"PDF-bytes-here")

    monkeypatch.setattr(
        "app.parsers.msg_reader.open_msg", lambda _p: _fake_msg(attachments=[att])
    )

    result = parse_msg(str(path))
    assert result["attachment_count"] == 1
    blobs = result["_attachments"]
    assert len(blobs) == 1
    assert blobs[0].filename == "report.pdf"
    assert blobs[0].content_bytes == b"PDF-bytes-here"


def test_parse_msg_corrupted_raises(tmp_path):
    """A CFB signature with no valid directory behind it must fail loudly.

    No mock: this drives the real olefile-backed reader, so it would catch a
    reader that returned an empty message instead of raising — the failure mode
    a stubbed library can never surface.
    """
    path = tmp_path / "bad.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    with pytest.raises(ValueError):
        parse_msg(str(path))


def test_parse_msg_rejects_a_file_that_is_not_ole_at_all(tmp_path):
    path = tmp_path / "plain.msg"
    path.write_bytes(b"just some text, definitely not a compound file")

    with pytest.raises(ValueError, match="not an OLE compound file"):
        parse_msg(str(path))


def test_parse_msg_dispatches_through_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
    path = tmp_path / "outlook.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr("app.parsers.msg_reader.open_msg", lambda _p: _fake_msg())

    result = parse_file(str(path))
    assert result["file_type"] == "msg"


# Silence unused-import warning — these are loaded for their side effect of
# triggering the @register_parser decorator runs at module import.
_ = (parse_mbox, parse_pptx, parse_ppt, parse_msg)
_ = patch  # imported for symmetry with sibling test files
