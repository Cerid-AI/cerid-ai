# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

r"""Gmail DataSource reply parsing.

The sibling google-workspace-mcp answers in PROSE, not JSON. The DataSource
was written against a structured API the server has never had, so every query
returned zero results and read as an empty mailbox — the failure was invisible
precisely because "no mail matched" is a legitimate answer.

The fixtures below are literal captures from the live server on 2026-08-09,
with ids and addresses replaced. Keep the ids NON-HEX: real Gmail ids are long
hex strings and `detect-secrets` reads those as "Hex High Entropy String",
failing the security gate on a fixture that contains no secret. The parser
matches `(\S+)` after the label, so the charset is not part of the contract —
the labels and layout are.
"""
from __future__ import annotations

from types import SimpleNamespace

from plugins.gmail.data_source import (
    parse_message_detail,
    parse_message_ids,
    tool_text,
)

SEARCH_REPLY = """Found 3 messages matching 'newer_than:30d':

📧 MESSAGES:
  1. Message ID: msgid-alpha-001
     Web Link: https://mail.google.com/mail/u/0/#all/msgid-alpha-001
     Thread ID: msgid-beta-002
     Thread Link: https://mail.google.com/mail/u/0/#all/msgid-beta-002

  2. Message ID: msgid-beta-002
     Web Link: https://mail.google.com/mail/u/0/#all/msgid-beta-002
     Thread ID: msgid-beta-002
     Thread Link: https://mail.google.com/mail/u/0/#all/msgid-beta-002
"""

DETAIL_REPLY = """Subject: Security alert
From: Google <no-reply@accounts.google.com>
Date: Mon, 10 Aug 2026 01:02:35 GMT
Message-ID: <voHU7OJP3Qy@notifications.google.com>
To: someone@example.com

--- BODY ---
You allowed Cerid AI access to some of your Google Account data

If you didn't allow this, review your account.
"""


class TestToolText:
    """``pool.call_tool`` returns a CallToolResult object. The old code
    type-checked for list/dict, matched neither, and dropped everything."""

    def test_unwraps_a_call_tool_result(self):
        raw = SimpleNamespace(content=[SimpleNamespace(text="hello")])
        assert tool_text(raw) == "hello"

    def test_joins_multiple_content_blocks(self):
        raw = SimpleNamespace(
            content=[SimpleNamespace(text="a"), SimpleNamespace(text="b")],
        )
        assert tool_text(raw) == "a\nb"

    def test_falls_back_to_structured_content(self):
        raw = SimpleNamespace(content=[], structuredContent={"result": "from-structured"})
        assert tool_text(raw) == "from-structured"

    def test_tolerates_plain_shapes_and_none(self):
        assert tool_text("plain") == "plain"
        assert tool_text({"content": [{"text": "d"}]}) == "d"
        assert tool_text(None) == ""
        assert tool_text(object()) == ""


class TestParseMessageIds:
    def test_extracts_every_message_id_in_order(self):
        msgs = parse_message_ids(SEARCH_REPLY)
        assert [m["id"] for m in msgs] == ["msgid-alpha-001", "msgid-beta-002"]

    def test_pairs_each_id_with_its_web_link_not_its_thread_link(self):
        msgs = parse_message_ids(SEARCH_REPLY)
        assert msgs[0]["web_link"].endswith("#all/msgid-alpha-001")
        assert all("Thread" not in m["web_link"] for m in msgs)

    def test_a_no_results_reply_yields_nothing(self):
        assert parse_message_ids("No messages found matching 'zzz'.") == []

    def test_ignores_an_id_that_is_not_a_numbered_result(self):
        # Anchored on the "N. Message ID:" prefix, so an id quoted inside a
        # body or subject cannot be mistaken for a hit.
        assert parse_message_ids("Message ID: deadbeef appears in the text") == []

    def test_survives_a_call_tool_result_wrapper(self):
        raw = SimpleNamespace(content=[SimpleNamespace(text=SEARCH_REPLY)])
        assert len(parse_message_ids(raw)) == 2


class TestParseMessageDetail:
    def test_extracts_headers(self):
        d = parse_message_detail(DETAIL_REPLY)
        assert d["subject"] == "Security alert"
        assert d["from"] == "Google <no-reply@accounts.google.com>"
        assert d["to"] == "someone@example.com"

    def test_extracts_the_body_after_the_separator(self):
        d = parse_message_detail(DETAIL_REPLY)
        assert d["body"].startswith("You allowed Cerid AI access")
        # The separator and headers must not bleed into the body.
        assert "--- BODY ---" not in d["body"]
        assert "Subject:" not in d["body"]

    def test_a_header_only_reply_still_parses(self):
        d = parse_message_detail("Subject: No body here\nFrom: a@b.c\n")
        assert d["subject"] == "No body here"
        assert "body" not in d

    def test_unparseable_input_is_empty_not_a_crash(self):
        assert parse_message_detail("") == {}
        assert parse_message_detail(None) == {}
