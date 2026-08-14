"""MS-OXMSG decoding, exercised against real bytes.

The ``.msg`` path used to be covered only by tests that replaced the entire
``extract_msg`` module with a ``MagicMock``. Those proved the dict-shape wiring
in ``parse_msg`` and nothing about parsing: no byte was ever decoded, so the
suite would have stayed green through any format bug.

``read_message`` is pure over a ``{stream-name: bytes}`` mapping precisely so it
can be tested for real. Every stream below is built the way Outlook builds it —
UTF-16LE for PT_UNICODE, cp1252 for PT_STRING8, little-endian FILETIME ticks in
a 16-byte property entry behind a 32-byte header.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("app.parsers.msg_reader")

from app.parsers.msg_reader import (  # noqa: E402
    PID_ATTACH_DATA,
    PID_ATTACH_LONG_FILENAME,
    PID_BODY,
    PID_CLIENT_SUBMIT_TIME,
    PID_DISPLAY_TO,
    PID_HTML,
    PID_INTERNET_MESSAGE_ID,
    PID_MESSAGE_DELIVERY_TIME,
    PID_SENDER_EMAIL,
    PID_SENDER_NAME,
    PID_SUBJECT,
    PT_BINARY,
    PT_STRING8,
    PT_SYSTIME,
    PT_UNICODE,
    read_message,
)

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def substg(prop_id: int, prop_type: int) -> str:
    return f"__substg1.0_{prop_id:04X}{prop_type:04X}"


def unicode_prop(prop_id: int, value: str) -> tuple[str, bytes]:
    return substg(prop_id, PT_UNICODE), value.encode("utf-16-le")


def string8_prop(prop_id: int, value: str) -> tuple[str, bytes]:
    return substg(prop_id, PT_STRING8), value.encode("cp1252")


def binary_prop(prop_id: int, value: bytes) -> tuple[str, bytes]:
    return substg(prop_id, PT_BINARY), value


def properties_stream(entries: dict[int, tuple[int, bytes]], header: int = 32) -> bytes:
    """Build a __properties_version1.0 stream: header, then 16-byte entries."""
    out = bytearray(b"\x00" * header)
    for prop_id, (prop_type, value) in entries.items():
        out += prop_type.to_bytes(2, "little")
        out += prop_id.to_bytes(2, "little")
        out += b"\x00" * 4  # flags
        out += value.ljust(8, b"\x00")[:8]
    return bytes(out)


def filetime(dt: datetime) -> bytes:
    ticks = int((dt - _FILETIME_EPOCH).total_seconds() * 10_000_000)
    return ticks.to_bytes(8, "little")


# ── Strings ─────────────────────────────────────────────────────────────────


def test_reads_unicode_properties():
    msg = read_message(
        dict(
            [
                unicode_prop(PID_SUBJECT, "Quarterly review"),
                unicode_prop(PID_SENDER_EMAIL, "alice@example.com"),
                unicode_prop(PID_DISPLAY_TO, "bob@example.com"),
                unicode_prop(PID_BODY, "Body text"),
                unicode_prop(PID_INTERNET_MESSAGE_ID, "<m-1@example.com>"),
            ]
        )
    )
    assert msg.subject == "Quarterly review"
    assert msg.sender == "alice@example.com"
    assert msg.to == "bob@example.com"
    assert msg.body == "Body text"
    assert msg.message_id == "<m-1@example.com>"


def test_non_ascii_survives_utf16_decoding():
    """The failure this catches is silent mojibake, not an exception."""
    msg = read_message(dict([unicode_prop(PID_SUBJECT, "Réunion — café ☕")]))
    assert msg.subject == "Réunion — café ☕"


def test_falls_back_to_string8_when_no_unicode_form():
    msg = read_message(dict([string8_prop(PID_SUBJECT, "Naïve café")]))
    assert msg.subject == "Naïve café"


def test_prefers_unicode_over_string8_when_both_present():
    streams = dict([string8_prop(PID_SUBJECT, "ansi"), unicode_prop(PID_SUBJECT, "unicode")])
    assert read_message(streams).subject == "unicode"


def test_truncated_utf16_does_not_raise():
    """Real messages from older clients carry odd-length string streams."""
    name = substg(PID_SUBJECT, PT_UNICODE)
    msg = read_message({name: "Hello".encode("utf-16-le") + b"\x00"})
    assert msg.subject is not None


def test_missing_properties_are_none_not_empty_string():
    msg = read_message({})
    assert msg.subject is None and msg.sender is None and msg.body is None
    assert msg.attachments == []


# ── Sender fallback ─────────────────────────────────────────────────────────


def test_sender_falls_back_to_display_name_when_address_absent():
    """Exchange-sent messages often carry only the display name."""
    msg = read_message(dict([unicode_prop(PID_SENDER_NAME, "Alice Example")]))
    assert msg.sender == "Alice Example"


def test_sender_prefers_the_address_over_the_display_name():
    streams = dict(
        [
            unicode_prop(PID_SENDER_NAME, "Alice Example"),
            unicode_prop(PID_SENDER_EMAIL, "alice@example.com"),
        ]
    )
    assert read_message(streams).sender == "alice@example.com"


# ── Dates ───────────────────────────────────────────────────────────────────


def test_submit_time_decodes_to_an_rfc2822_date():
    sent = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    streams = {
        "__properties_version1.0": properties_stream(
            {PID_CLIENT_SUBMIT_TIME: (PT_SYSTIME, filetime(sent))}
        )
    }
    msg = read_message(streams)
    assert msg.date is not None
    assert "2 Jan 2026 03:04:05" in msg.date


def test_delivery_time_is_used_when_submit_time_is_absent():
    delivered = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    streams = {
        "__properties_version1.0": properties_stream(
            {PID_MESSAGE_DELIVERY_TIME: (PT_SYSTIME, filetime(delivered))}
        )
    }
    assert "4 Mar 2026" in (read_message(streams).date or "")


def test_zero_and_absurd_filetimes_yield_no_date_rather_than_crashing():
    for value in (b"\x00" * 8, b"\xff" * 8):
        streams = {
            "__properties_version1.0": properties_stream(
                {PID_CLIENT_SUBMIT_TIME: (PT_SYSTIME, value)}
            )
        }
        assert read_message(streams).date is None


def test_properties_header_is_skipped_not_parsed_as_an_entry():
    """A wrong header size shifts every field; this is what would catch it."""
    sent = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    good = properties_stream({PID_CLIENT_SUBMIT_TIME: (PT_SYSTIME, filetime(sent))})
    assert "15 Jun 2026" in (read_message({"__properties_version1.0": good}).date or "")
    # Same entries with a mis-sized header must NOT accidentally still resolve.
    bad = properties_stream(
        {PID_CLIENT_SUBMIT_TIME: (PT_SYSTIME, filetime(sent))}, header=8
    )
    assert read_message({"__properties_version1.0": bad}).date is None


# ── HTML body ───────────────────────────────────────────────────────────────


def test_html_body_read_from_the_binary_property():
    streams = dict([binary_prop(PID_HTML, b"<p>Hi</p>")])
    assert read_message(streams).html_body == "<p>Hi</p>"


def test_html_body_decodes_non_utf8_bytes_instead_of_mojibake():
    """WB-07: PidTagHtml is codepage-dependent 8-bit text (Outlook's message
    codepage), not necessarily UTF-8. Forcing UTF-8 turned every non-ASCII
    byte into U+FFFD; this must fall back to the same cp1252 heuristic
    ``_decode_string`` already uses for PT_STRING8 properties."""
    html = "<p>Café naïve</p>".encode("cp1252")
    streams = dict([binary_prop(PID_HTML, html)])
    assert read_message(streams).html_body == "<p>Café naïve</p>"


def test_plain_body_and_html_body_are_independent():
    streams = dict([unicode_prop(PID_BODY, "plain"), binary_prop(PID_HTML, b"<p>rich</p>")])
    msg = read_message(streams)
    assert msg.body == "plain" and msg.html_body == "<p>rich</p>"


# ── Attachments ─────────────────────────────────────────────────────────────


def test_attachments_carry_bytes_and_long_filename():
    att = dict(
        [
            binary_prop(PID_ATTACH_DATA, b"%PDF-1.7 bytes"),
            unicode_prop(PID_ATTACH_LONG_FILENAME, "quarterly report.pdf"),
        ]
    )
    msg = read_message({}, [att])
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "quarterly report.pdf"
    assert msg.attachments[0].data == b"%PDF-1.7 bytes"


def test_attachment_without_data_is_skipped():
    """A metadata-only attachment storage must not become an empty blob."""
    att = dict([unicode_prop(PID_ATTACH_LONG_FILENAME, "ghost.pdf")])
    assert read_message({}, [att]).attachments == []


def test_attachment_without_a_name_is_still_surfaced():
    att = dict([binary_prop(PID_ATTACH_DATA, b"data")])
    assert read_message({}, [att]).attachments[0].filename == "(unnamed)"


def test_multiple_attachments_preserve_input_order():
    atts = [
        dict([binary_prop(PID_ATTACH_DATA, b"a"), unicode_prop(PID_ATTACH_LONG_FILENAME, "a.txt")]),
        dict([binary_prop(PID_ATTACH_DATA, b"b"), unicode_prop(PID_ATTACH_LONG_FILENAME, "b.txt")]),
    ]
    names = [a.filename for a in read_message({}, atts).attachments]
    assert names == ["a.txt", "b.txt"]
