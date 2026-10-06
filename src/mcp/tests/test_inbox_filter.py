# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Provider headers and a loopback rspamd score. The model is the boundary."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.data_sources.base import DataSourceResult
from core.agents.inbox_filter import (
    SOCIAL_CONFIDENCE,
    apply_verdict,
    filter_verdict,
    reconstructed_message,
    rspamd_is_spam,
    signal_note,
)
from core.agents.inbox_rspamd import rspamd_url, symbol_trace
from core.agents.inbox_triage import _categorize_thread, _heuristic_categorize, set_inbox_memory, set_inbox_rspamd


def _msg(subject: str, body: str, **metadata: str) -> DataSourceResult:
    meta = {"provider_thread_id": "thr-1", "provider_message_id": "msg-1"}
    meta.update(metadata)
    return DataSourceResult(
        title=subject,
        content=f"From: ops@example.com\nSubject: {subject}\n\n{body}",
        source_url="mailto:ops@example.com",
        source_name="ops@example.com",
        confidence=0.8,
        metadata=meta,
    )


def _reject() -> dict[str, object]:
    return {"action": "reject", "score": 20, "required_score": 15, "is_skipped": False}


class TestFilterVerdict:
    def test_a_phrase_hit_is_spam_before_an_urgent_word(self):
        verdict = filter_verdict("URGENT: your account has been suspended", subject="URGENT")
        assert verdict.category == "spam"
        assert verdict.sticks is True

    def test_a_list_id_is_a_newsletter_unless_the_subject_is_urgent(self):
        quiet = filter_verdict(
            "critical update inside the digest",
            subject="Weekly notes",
            headers={"list_id": "<news.example.com>"},
        )
        assert quiet.category == "newsletter" and quiet.sticks is True
        loud = filter_verdict(
            "host critical",
            subject="host critical",
            headers={"list_id": "<news.example.com>"},
        )
        assert loud.category == "urgent" and loud.sticks is False

    def test_a_sale_with_a_list_header_stays_promo(self):
        verdict = filter_verdict(
            "50% off unsubscribe",
            subject="Sale",
            headers={"list_unsubscribe": "<mailto:leave@example.com>"},
        )
        assert verdict.category == "promo" and verdict.sticks is True

    def test_gmail_promotions_files_as_promo(self):
        verdict = filter_verdict(
            "hello",
            subject="Hello",
            headers={"labels": "INBOX, CATEGORY_PROMOTIONS"},
        )
        assert verdict.category == "promo" and verdict.sticks is True

    def test_rspamd_reject_is_spam_unless_the_text_is_a_bill(self):
        spam = filter_verdict("host critical", subject="host critical", rspamd=_reject())
        assert spam.category == "spam" and spam.sticks is True
        bill = filter_verdict(
            "Your bill is ready. Amount due $40",
            subject="Invoice",
            rspamd=_reject(),
        )
        assert bill.category != "spam" and bill.sticks is False

    def test_add_header_does_not_archive(self):
        verdict = filter_verdict(
            "hello",
            subject="Hello",
            rspamd={"action": "add header", "score": 7, "required_score": 15},
        )
        assert verdict.category == "actionable" and verdict.sticks is False
        assert verdict.confidence < 0.8

    def test_dmarc_fail_does_not_file_spam(self):
        verdict = filter_verdict(
            "host critical",
            subject="host critical",
            headers={"authentication_results": "mx; dmarc=fail"},
        )
        assert verdict.category == "urgent" and verdict.sticks is False
        assert verdict.confidence < 0.8

    def test_a_passing_dmarc_is_not_a_fail(self):
        verdict = filter_verdict(
            "host critical",
            subject="host critical",
            headers={"authentication_results": "mx; dmarc=pass"},
        )
        assert verdict.confidence >= 0.8

    def test_apply_replaces_a_disagreeing_model(self):
        verdict = filter_verdict("hello", subject="Hello", headers={"labels": "CATEGORY_PROMOTIONS"})
        parsed = apply_verdict(
            {"category": "actionable", "action": "keep", "utility": "correspondence", "confidence": 0.4},
            verdict,
            "hello",
        )
        assert parsed["category"] == "promo"
        assert parsed["action"] == "archive"

    def test_a_morning_brief_body_is_not_urgent(self):
        verdict = filter_verdict("critical path in the notes", subject="Morning brief")
        assert verdict.category != "urgent"

    def test_wholesale_is_not_promo(self):
        verdict = filter_verdict("wholesale pricing", subject="Weekly notes")
        assert verdict.category != "promo"

    def test_you_have_won_beats_an_urgent_subject(self):
        verdict = filter_verdict("you've won a trip", subject="URGENT")
        assert verdict.category == "spam"
        assert verdict.sticks is True
        assert verdict.reason == "phrase:you've won"

    def test_a_sale_without_a_list_header_sticks(self):
        verdict = filter_verdict("a sale this week", subject="Hello")
        parsed = apply_verdict(
            {"category": "actionable", "action": "keep", "utility": "correspondence", "confidence": 0.4},
            verdict,
            "a sale this week",
        )
        assert verdict.sticks is True
        assert parsed["category"] == "promo"
        assert parsed["action"] == "archive"
        assert verdict.reason == "phrase:sale"

    def test_a_digest_without_a_list_header_sticks(self):
        verdict = filter_verdict("the weekly digest", subject="Hello")
        assert verdict.category == "newsletter"
        assert verdict.sticks is True
        assert verdict.reason == "phrase:weekly digest"

    def test_a_reply_is_personal_and_does_not_stick(self):
        verdict = filter_verdict(
            "thanks for the note",
            subject="Re: dinner",
            headers={"in_reply_to": "<prev@example.com>"},
        )
        assert verdict.category == "personal"
        assert verdict.sticks is False
        assert verdict.confidence == SOCIAL_CONFIDENCE
        assert verdict.reason == "header:in-reply-to"
        kept = apply_verdict(
            {"category": "personal", "action": "keep", "utility": "correspondence"},
            verdict,
            "thanks for the note",
        )
        assert kept["action"] == "keep"

    def test_a_list_reply_stays_a_newsletter(self):
        verdict = filter_verdict(
            "thanks",
            subject="Re: Weekly notes",
            headers={"list_id": "<news.example.com>", "in_reply_to": "<prev@example.com>"},
        )
        assert verdict.category == "newsletter"
        assert verdict.sticks is True


class TestReconstruction:
    def test_headers_cannot_break_out_of_the_field(self):
        raw = reconstructed_message(
            sender="a@example.com\nBcc: hidden@example.com",
            subject="Hello",
            body="body",
            message_id="m-1",
        )
        text = raw.decode()
        assert not any(line.startswith("Bcc:") for line in text.splitlines())
        assert text.startswith("From: a@example.com ")
        assert "Message-ID: <m-1>" in text
        assert "undisclosed-recipients" not in text
        assert not any(line.startswith("To:") for line in text.splitlines())

    def test_a_real_recipient_and_date_are_copied(self):
        raw = reconstructed_message(
            sender="a@example.com",
            subject="Hello",
            body="body",
            to="me@example.com",
            date="Tue, 1 Oct 2026 00:00:00 +0000",
            message_id="<real@example.com>",
        )
        text = raw.decode()
        assert "To: me@example.com" in text
        assert "Date: Tue, 1 Oct 2026 00:00:00 +0000" in text
        assert "Message-ID: <real@example.com>" in text
        assert "undisclosed-recipients" not in text

    def test_a_newline_in_to_cannot_open_a_header(self):
        raw = reconstructed_message(
            sender="a@example.com",
            subject="Hello",
            body="body",
            to="me@example.com\nBcc: hidden@example.com",
        )
        text = raw.decode()
        assert not any(line.startswith("Bcc:") for line in text.splitlines())
        assert "To: me@example.com Bcc: hidden@example.com" in text

    def test_structural_symbols_do_not_ask_for_review(self):
        note = signal_note({}, {
            "action": "add header",
            "score": 3.5,
            "symbols": {"R_UNDISC_RCPT": 3.0, "MIME_GOOD": -0.1},
        })
        assert "Rspamd: review" not in note

    def test_a_content_symbol_still_asks_for_review(self):
        note = signal_note({}, {
            "action": "add header",
            "score": 6.5,
            "symbols": {"MANY_INVISIBLE_PARTS": 1.0, "MIME_GOOD": -0.1},
        })
        assert "Rspamd: review" in note

    def test_reject_is_still_named(self):
        note = signal_note({}, {
            "action": "reject",
            "score": 20,
            "required_score": 15,
            "symbols": {"R_UNDISC_RCPT": 3.0},
        })
        assert "Rspamd: reject" in note

    def test_symbol_trace_drops_options_and_keeps_eight(self):
        symbols = {f"S{index}": {"score": float(index), "options": ["http://secret.example/x"]} for index in range(10)}
        traced = symbol_trace({"symbols": symbols})
        assert len(traced) == 8
        assert traced[0][0] == "S9"
        assert all("http" not in name for name, _score in traced)

    def test_rspamd_spam_requires_reject_or_the_threshold(self):
        assert rspamd_is_spam(_reject()) is True
        assert rspamd_is_spam({"action": "no action", "score": 1, "required_score": 15}) is False
        assert rspamd_is_spam({"action": "add header", "score": 15, "required_score": 15}) is True


class TestRspamdUrl:
    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv("CERID_RSPAMD_URL", raising=False)
        assert rspamd_url() == ""

    def test_loopback_http_is_kept(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", "http://127.0.0.1:11333")
        assert rspamd_url() == "http://127.0.0.1:11333"

    def test_a_public_host_is_ignored(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", "http://rspamd.example.com/checkv2")
        assert rspamd_url() == ""

    def test_userinfo_is_ignored(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", "http://user:pw@127.0.0.1:11333")  # pragma: allowlist secret
        assert rspamd_url() == ""


class TestCategorize:
    async def test_rspamd_reject_overrides_the_model(self):
        async def scan(_raw: bytes) -> dict[str, object]:
            return _reject()

        set_inbox_rspamd(scan)
        try:
            with patch(
                "core.utils.internal_llm.call_internal_llm",
                new_callable=AsyncMock,
                return_value='{"category":"urgent","summary":"host","suggested_action":"page","action":"keep"}',
            ):
                result = await _categorize_thread("thr-1", [_msg("host critical", "disk full")])
        finally:
            set_inbox_rspamd(None)
        assert result["category"] == "spam"
        assert result["action"] == "archive"

    async def test_a_pin_is_not_scanned_or_overridden(self):
        async def scan(_raw: bytes) -> dict[str, object]:
            raise AssertionError("pinned mail is not scanned")

        def lookup(source: str, sender: str, subject: str, list_id: str = "") -> dict[str, object]:
            del source, sender, subject, list_id
            return {
                "memory_action": "keep",
                "memory_category": "personal",
                "memory_confidence": 0.9,
            }

        set_inbox_rspamd(scan)
        set_inbox_memory(lookup)
        try:
            result = await _categorize_thread("thr-1", [_msg("host critical", "disk full")])
        finally:
            set_inbox_rspamd(None)
            set_inbox_memory(None)
        assert result["category"] == "personal"
        assert result["action"] == "keep"
        assert result["band"] == "skip"
        assert "classification_reason" not in result

    async def test_a_list_id_overrides_an_actionable_model(self):
        set_inbox_rspamd(None)
        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            return_value='{"category":"actionable","summary":"notes","suggested_action":"read","action":"keep"}',
        ):
            result = await _categorize_thread(
                "thr-1",
                [_msg("Weekly notes", "here is the digest", list_id="<news.example.com>")],
            )
        assert result["category"] == "newsletter"
        assert result["action"] == "archive"

    def test_fallback_knows_spam_and_list_headers(self):
        spam = _heuristic_categorize(
            [_msg("Hello", "your account has been suspended")],
            "hello",
        )
        assert spam["category"] == "spam"
        listed = _heuristic_categorize(
            [_msg("Notes", "the usual roundup", list_id="<news.example.com>")],
            "notes",
        )
        assert listed["category"] == "newsletter"

    async def test_a_sale_sticks_and_records_why(self):
        set_inbox_rspamd(None)

        async def _boom(*_args, **_kwargs):
            raise AssertionError("a sticking verdict does not call a model")

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_boom,
        ):
            result = await _categorize_thread("thr-1", [_msg("Hello", "a sale this week")])
        assert result["category"] == "promo"
        assert result["action"] == "archive"
        assert result["band"] == "skip"
        assert result["classification_reason"] == "phrase:sale"
        assert result["model"] == ""

    async def test_rspamd_sees_the_rfc_id_and_not_the_provider_id(self):
        seen: dict[str, bytes] = {}

        async def scan(raw: bytes) -> dict[str, object]:
            seen["raw"] = raw
            return {"action": "no action", "score": 0, "required_score": 15}

        set_inbox_rspamd(scan)
        try:
            with patch(
                "core.utils.internal_llm.call_internal_llm",
                new_callable=AsyncMock,
                return_value='{"category":"actionable","summary":"hi","suggested_action":"read","action":"keep"}',
            ):
                await _categorize_thread(
                    "thr-1",
                    [_msg(
                        "Hello",
                        "just a note",
                        to="me@example.com",
                        date="Tue, 1 Oct 2026 00:00:00 +0000",
                        rfc_message_id="<real@example.com>",
                    )],
                )
        finally:
            set_inbox_rspamd(None)
        text = seen["raw"].decode()
        assert "To: me@example.com" in text
        assert "Message-ID: <real@example.com>" in text
        assert "<msg-1>" not in text
        assert "undisclosed-recipients" not in text
