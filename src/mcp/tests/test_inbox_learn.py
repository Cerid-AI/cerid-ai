# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sender memory, rules, and mailbox corrections. No live mailbox."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.data_sources.base import DataSourceResult
from app.inbox.learn import signals_for
from app.inbox.ledger import InboxLedger
from app.inbox.review import record_and_apply
from core.agents.inbox_actions import operator_outcome, proposed_outcome
from core.agents.inbox_triage import (
    TriagedThread,
    _categorize_thread,
    set_inbox_accounts,
    set_inbox_memory,
)

_SENDER = "alice@example.com"


@pytest.fixture(autouse=True)
def _reset_memory():
    set_inbox_memory(None)
    set_inbox_accounts(None)
    yield
    set_inbox_memory(None)
    set_inbox_accounts(None)


def _msg(subject: str, body: str, sender: str = _SENDER) -> DataSourceResult:
    return DataSourceResult(
        title=subject,
        content=body,
        source_url=f"mailto:{sender}",
        source_name=sender,
        confidence=0.8,
    )


def _applied(action: str = "archive", category: str = "newsletter") -> dict:
    return {
        "provider": "gmail",
        "action": action,
        "category": category,
        "receipt": {"calls": [{"arguments": {"remove_label_ids": ["INBOX"]}}]},
    }


class TestOperatorOutcome:
    def test_a_cleared_gmail_label_back_in_the_inbox_is_keep_actionable(self):
        outcome = operator_outcome(_applied(), {"labels": ["INBOX"]})
        assert outcome == ("keep", "actionable")

    def test_one_replacement_label_is_that_category(self):
        outcome = operator_outcome(_applied(), {"labels": ["INBOX", "Cerid/Personal"]})
        assert outcome == ("keep", "personal")

    def test_several_cerid_labels_are_not_learned(self):
        outcome = operator_outcome(
            _applied(),
            {"labels": ["INBOX", "Cerid/Personal", "Cerid/Action"]},
        )
        assert outcome is None

    def test_the_same_outcome_is_not_a_correction(self):
        decision = _applied(action="keep", category="newsletter")
        decision["receipt"] = {"calls": []}
        assert operator_outcome(decision, {"labels": ["INBOX", "Cerid/Newsletter"]}) is None

    def test_a_missing_label_key_is_unknown(self):
        assert operator_outcome(_applied(), {}) is None

    def test_outlook_cleared_category_in_the_inbox(self):
        decision = {"provider": "outlook", "action": "archive", "category": "newsletter", "receipt": {}}
        assert operator_outcome(decision, {"categories": "", "folder": "inbox"}) == ("keep", "actionable")

    def test_apple_flag_cleared_in_the_inbox(self):
        decision = {"provider": "apple_mail", "action": "keep", "category": "urgent", "receipt": {}}
        assert operator_outcome(decision, {"flag": "", "mailbox": "INBOX"}) == ("keep", "actionable")

    def test_apple_mailbox_restored_keeps_the_applied_category_when_the_flag_is_unknown(self):
        decision = {
            "provider": "apple_mail",
            "action": "archive",
            "category": "newsletter",
            "receipt": {"calls": [{"arguments": {"argv": ["move", "m1", "Archive"]}}]},
        }
        assert operator_outcome(decision, {"mailbox": "INBOX"}) == ("keep", "newsletter")

    def test_mark_read_does_not_treat_an_empty_flag_as_a_correction(self):
        decision = {"provider": "apple_mail", "action": "mark_read", "category": "personal", "receipt": {}}
        assert operator_outcome(decision, {"flag": ""}) is None


class TestProposedOutcome:
    """The user's own move against a proposal nothing applied. Sitting still is not a move."""

    def _proposed(self, provider: str, action: str = "keep", category: str = "actionable") -> dict:
        return {"provider": provider, "action": action, "category": category, "receipt": {}}

    def test_gmail_archived_by_hand_is_archive(self):
        assert proposed_outcome(self._proposed("gmail"), {"labels": ""}) == ("archive", "actionable")

    def test_gmail_still_in_the_inbox_is_not_a_move(self):
        assert proposed_outcome(self._proposed("gmail"), {"labels": "INBOX"}) is None
        assert proposed_outcome(self._proposed("gmail", "archive", "newsletter"), {"labels": "INBOX"}) is None

    def test_gmail_hand_label_is_that_category(self):
        assert proposed_outcome(self._proposed("gmail"), {"labels": "INBOX,Cerid/Personal"}) == ("keep", "personal")

    def test_several_labels_are_not_learned(self):
        observation = {"labels": "INBOX,Cerid/Personal,Cerid/Urgent"}
        assert proposed_outcome(self._proposed("gmail"), observation) is None

    def test_unknown_location_is_unknown(self):
        assert proposed_outcome(self._proposed("gmail"), {}) is None
        assert proposed_outcome(self._proposed("apple_mail"), {"flag": ""}) is None

    def test_apple_archived_by_hand(self):
        assert proposed_outcome(self._proposed("apple_mail"), {"mailbox": "Archive"}) == ("archive", "actionable")

    def test_apple_flag_set_by_hand(self):
        outcome = proposed_outcome(self._proposed("apple_mail"), {"mailbox": "INBOX", "flag": "blue"})
        assert outcome == ("keep", "personal")
        assert proposed_outcome(self._proposed("apple_mail"), {"mailbox": "INBOX", "flag": ""}) is None

    def test_outlook_category_set_by_hand(self):
        decision = self._proposed("outlook", "archive", "newsletter")
        assert proposed_outcome(decision, {"folder": "inbox", "categories": ""}) is None
        assert proposed_outcome(decision, {"folder": "inbox", "categories": "Cerid/Urgent"}) == ("keep", "urgent")


def test_three_corrections_pin_and_a_different_outcome_resets(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    row = {}
    for _ in range(3):
        row = ledger.note_correction(
            source="gmail", sender="A@Example.com", category="newsletter", action="archive",
        )
    assert row["hits"] == 3
    assert row["pinned"] == 1
    assert row["sender"] == "a@example.com"
    assert row["domain"] == "example.com"
    reset = ledger.note_correction(
        source="gmail", sender="a@example.com", category="personal", action="keep",
    )
    assert reset["hits"] == 1
    assert reset["pinned"] == 0


def test_one_applied_decision_is_one_hit(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    first = ledger.note_correction(
        source="gmail", sender=_SENDER, category="personal", action="keep", decision_id="d1",
    )
    second = ledger.note_correction(
        source="gmail", sender=_SENDER, category="personal", action="keep", decision_id="d1",
    )
    assert first["hits"] == 1
    assert second["hits"] == 1


def test_a_manual_pin_is_immediate_and_not_sticky_across_a_new_outcome(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    pinned = ledger.pin_sender(source="gmail", sender=_SENDER, category="newsletter", action="archive")
    assert pinned["pinned"] == 1
    assert pinned["hits"] >= 3
    reset = ledger.note_correction(
        source="gmail", sender=_SENDER, category="personal", action="keep",
    )
    assert reset["pinned"] == 0
    assert reset["hits"] == 1


def test_rules_prefer_the_provider_and_then_more_fields(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.upsert_rule(
        rule_id="wide",
        source="*",
        condition={"from": _SENDER},
        action="archive",
        category="newsletter",
    )
    ledger.upsert_rule(
        rule_id="exact",
        source="gmail",
        condition={"from": _SENDER},
        action="keep",
        category="personal",
    )
    signals = signals_for(ledger, "gmail", _SENDER, "Hello")
    assert signals["rule_action"] == "keep"
    assert signals["rule_category"] == "personal"
    ledger.upsert_rule(
        rule_id="specific",
        source="gmail",
        condition={"from": _SENDER, "subject_prefix": "Meet"},
        action="keep",
        category="actionable",
    )
    matched = signals_for(ledger, "gmail", _SENDER, "Meeting notes")
    assert matched["rule_category"] == "actionable"
    missed = signals_for(ledger, "gmail", _SENDER, "Mail: Meeting notes")
    assert missed["rule_category"] == "personal"
    with pytest.raises(ValueError):
        ledger.upsert_rule(
            rule_id="bad",
            source="gmail",
            condition={"forward": "x"},
            action="keep",
            category="personal",
        )


async def test_a_pinned_sender_skips_the_model(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    for _ in range(3):
        ledger.note_correction(source="gmail", sender=_SENDER, category="newsletter", action="archive")

    def lookup(source: str, sender: str, subject: str, list_id: str = "") -> dict:
        return signals_for(ledger, source, sender, subject, list_id)

    set_inbox_memory(lookup)
    mock = AsyncMock(return_value='{"category":"urgent","summary":"x","action":"keep","confidence":0.99}')
    with patch("core.utils.internal_llm.call_internal_llm", mock):
        result = await _categorize_thread("t", [_msg("Sale", "unsubscribe")], source="gmail")
    mock.assert_not_called()
    assert result["band"] == "skip"
    assert result["action"] == "archive"
    assert result["category"] == "newsletter"
    assert "draft_body" not in result


def _pin_draft(ledger: InboxLedger) -> None:
    ledger.pin_sender(source="gmail", sender=_SENDER, category="actionable", action="draft")
    set_inbox_memory(lambda source, sender, subject, list_id="": signals_for(
        ledger, source, sender, subject, list_id,
    ))


async def test_a_pinned_draft_skips_classification_and_still_writes_the_reply(tmp_path: Path):
    """A pin skips the classification model. A draft is a second model call and still runs."""
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    _pin_draft(ledger)
    mock = AsyncMock(return_value='{"draft": "Yes, 3pm works for the meeting time."}')
    with patch("core.utils.internal_llm.call_internal_llm", mock):
        result = await _categorize_thread(
            "t", [_msg("Meeting", "Can you confirm the meeting time?")], source="gmail",
        )
    assert mock.await_count == 1
    assert mock.await_args.kwargs["stage"] == "inbox_triage_review"
    assert "category is one of" not in mock.await_args.args[0][0]["content"]
    assert result["action"] == "draft"
    assert result["category"] == "actionable"
    assert result["draft_body"] == "Yes, 3pm works for the meeting time."
    assert result["band"] == "local-chat"
    assert "classification_reason" not in result


async def test_a_pinned_draft_that_fails_locally_stays_needs_review_under_local_only(
    monkeypatch, tmp_path: Path,
):
    import config

    monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    _pin_draft(ledger)
    mock = AsyncMock(return_value='{"draft": ""}')
    with patch("core.utils.internal_llm.call_internal_llm", mock):
        result = await _categorize_thread(
            "t", [_msg("Meeting", "Can you confirm the meeting time?")], source="gmail",
        )
    assert mock.await_count == 1
    assert result["action"] == "draft"
    assert result["band"] == "needs_review"
    assert "draft_body" not in result


async def test_a_sender_rule_skips_without_memory(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.upsert_rule(
        rule_id="from-alice",
        source="gmail",
        condition={"from": _SENDER},
        action="archive",
        category="promo",
    )
    set_inbox_memory(lambda source, sender, subject, list_id="": signals_for(
        ledger, source, sender, subject, list_id,
    ))
    mock = AsyncMock()
    with patch("core.utils.internal_llm.call_internal_llm", mock):
        result = await _categorize_thread("t", [_msg("Hello", "just checking")], source="gmail")
    mock.assert_not_called()
    assert result["band"] == "skip"
    assert result["action"] == "archive"
    assert result["category"] == "promo"


def test_wiring_memory_does_not_open_the_ledger(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.agents_di import wire_inbox_triage_di
    from app.inbox.learn import lookup_signals
    from core.agents.inbox_triage import get_inbox_accounts

    set_inbox_memory(lookup_signals)
    wire_inbox_triage_di()
    assert get_inbox_accounts() is not None
    assert not (tmp_path / "inbox.sqlite").exists()


def test_included_addresses_leave_out_removed_and_excluded_rows(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.inbox.review import included_addresses, open_ledger

    ledger = open_ledger()
    ledger.upsert_account(provider="gmail", address="Kept@example.com", included=True)
    ledger.upsert_account(provider="gmail", address="off@example.com", included=False)
    ledger.upsert_account(provider="gmail", address="gone@example.com", included=True)
    ledger.remove_account("gmail", "gone@example.com")
    ledger.upsert_account(provider="outlook", address="other@example.com", included=True)
    assert included_addresses("gmail") == ["kept@example.com"]
    assert included_addresses("apple_mail") == []


async def test_pin_and_rule_tools_are_gated_before_the_ledger(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.mcp_tools.inbox import pkb_inbox_rule_upsert, pkb_inbox_sender_pin

    with patch("config.features.is_feature_enabled", return_value=False):
        pinned = await pkb_inbox_sender_pin("gmail", _SENDER, "archive", "newsletter")
        ruled = await pkb_inbox_rule_upsert(
            condition={"from": _SENDER}, action="archive", category="newsletter",
        )
    assert pinned["status"] == "feature_gated"
    assert ruled["status"] == "feature_gated"
    assert not (tmp_path / "inbox.sqlite").exists()


async def test_reconcile_learns_one_hit_and_does_not_repeat_it(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = ledger.propose(
        source="gmail",
        provider_thread_id="thread-9",
        account_address="a@example.com",
        message_ids=["m1"],
        category="newsletter",
        utility="none",
        action="archive",
        band="local-small",
    )
    ledger.record(
        decision_id,
        status="applied",
        receipt={"calls": [{"arguments": {"remove_label_ids": ["INBOX"], "add_label_ids": ["Cerid/Newsletter"]}}]},
    )
    thread = TriagedThread(
        thread_id="thread-9",
        source="gmail",
        participants=[_SENDER],
        subject="News",
        message_count=1,
        latest_at="0",
        category="newsletter",
        summary="news",
        suggested_action="archive",
        sender=_SENDER,
        observation={"labels": "INBOX,Cerid/Personal"},
    )
    result = type("R", (), {"threads": [thread]})()
    first = await record_and_apply(result, ledger=ledger)
    second = await record_and_apply(result, ledger=ledger)
    assert first["learned"] == 1
    assert second["learned"] == 0
    stored = ledger.get_sender("gmail", _SENDER)
    assert stored is not None
    assert stored["hits"] == 1
    assert stored["action"] == "keep"
    assert stored["category"] == "personal"
    assert stored["last_decision"] == decision_id


async def test_a_hand_archive_of_a_proposed_keep_is_learned_with_actions_off(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.delenv("CERID_INBOX_ACTIONS_ENABLED", raising=False)
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.upsert_account(provider="apple_mail", address="me@example.com", included=True)
    thread = TriagedThread(
        thread_id="<m1@example.com>",
        source="apple_mail",
        participants=[_SENDER],
        subject="Plan",
        message_count=1,
        latest_at="0",
        category="actionable",
        summary="plan",
        suggested_action="review",
        action="keep",
        writable=True,
        message_ids=["<m1@example.com>"],
        account="me@example.com",
        mailbox="INBOX",
        sender=_SENDER,
        observation={"mailbox": "INBOX"},
    )
    seen = type("R", (), {"threads": [thread]})()
    first = await record_and_apply(seen, ledger=ledger)
    assert first["learned"] == 0 and first["proposed"] == 1
    assert ledger.get_sender("apple_mail", _SENDER) is None

    thread.mailbox = "Archive"
    thread.observation = {"mailbox": "Archive"}
    second = await record_and_apply(seen, ledger=ledger)
    assert second["learned"] == 1
    stored = ledger.get_sender("apple_mail", _SENDER)
    assert stored is not None
    assert (stored["action"], stored["category"], stored["hits"]) == ("archive", "actionable", 1)

    third = await record_and_apply(seen, ledger=ledger)
    assert third["learned"] == 0
    assert ledger.get_sender("apple_mail", _SENDER)["hits"] == 1
    assert ledger.list_decisions(status="applied") == []


def test_two_hits_do_not_reach_the_skip_band(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.note_correction(source="gmail", sender=_SENDER, category="personal", action="keep")
    ledger.note_correction(source="gmail", sender=_SENDER, category="personal", action="keep")
    signals = signals_for(ledger, "gmail", _SENDER, "Hello")
    assert signals["memory_confidence"] < 0.9
    assert signals["memory_confidence"] == 0.6
