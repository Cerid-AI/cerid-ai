# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Review queue: apply, undo, and the capped auto-apply pass. Fakes stop at the transport."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.inbox.ledger import InboxLedger
from app.inbox.review import (
    add_account,
    apply_ids,
    auto_apply_pass,
    change_account,
    discovered_addresses,
    open_ledger,
    propose_triage,
    record_and_apply,
    skip_decision,
    undo_decision,
)
from app.mcp_tools.inbox import pkb_inbox_apply, pkb_inbox_undo
from core.agents.inbox_actions import LABEL_NAME
from core.agents.inbox_triage import TriagedThread, _apply_fields


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
        self.calls.append({"provider": provider, "tool": tool, "arguments": arguments})
        return "{}"

    async def apple(self, argv: list[str]) -> tuple[int, dict]:
        self.calls.append({"provider": "apple_mail", "tool": argv[0], "arguments": {"argv": list(argv)}})
        return 0, {"ok": True}


class ScriptedTransport(RecordingTransport):
    def __init__(self, tools: dict[str, object]) -> None:
        super().__init__()
        self._tools = tools

    async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
        self.calls.append({"provider": provider, "tool": tool, "arguments": arguments})
        reply = self._tools[tool]
        if callable(reply):
            return str(reply(arguments))
        return str(reply)


def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InboxLedger:
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.delenv("CERID_INBOX_ACTIONS_ENABLED", raising=False)
    return InboxLedger(tmp_path / "inbox.sqlite")


def _propose(ledger: InboxLedger, **overrides: object) -> str:
    fields: dict[str, object] = {
        "source": "outlook",
        "provider_thread_id": "t1",
        "account_address": "a@example.com",
        "message_ids": ["m1"],
        "category": "newsletter",
        "utility": "none",
        "action": "mark_read",
        "band": "local-small",
        "mailbox_before": "INBOX",
    }
    fields.update(overrides)
    return ledger.propose(**fields)


def _account(ledger: InboxLedger, **overrides: object) -> None:
    fields: dict[str, object] = {
        "provider": "outlook",
        "address": "a@example.com",
        "included": True,
        "folder_sort": False,
    }
    fields.update(overrides)
    ledger.upsert_account(**fields)  # type: ignore[arg-type]


def _thread(**overrides: object) -> TriagedThread:
    fields: dict[str, object] = {
        "thread_id": "t1",
        "source": "gmail",
        "participants": [],
        "subject": "Hi",
        "message_count": 1,
        "latest_at": "0",
        "category": "newsletter",
        "summary": "s",
        "suggested_action": "archive",
        "utility": "none",
        "action": "archive",
        "writable": True,
        "message_ids": ["m1"],
        "account": "a@example.com",
    }
    fields.update(overrides)
    return TriagedThread(**fields)  # type: ignore[arg-type]


def _gmail_transport() -> ScriptedTransport:
    def _created(arguments: dict) -> str:
        return f"Label created successfully!\nName: {arguments['name']}\nID: Label_9"

    return ScriptedTransport({
        "list_gmail_labels": "Labels:\n  • Old (ID: Label_1)\n",
        "manage_gmail_label": _created,
        "batch_modify_gmail_message_labels": "Labels updated.",
    })


def _call_args(result: dict) -> list[dict]:
    return [call["arguments"] for call in result["calls"]]


@pytest.mark.asyncio
async def test_apply_dry_run_is_the_default_and_does_not_call_transport(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_ids([decision_id], ledger=ledger, transport=transport)
    assert result["dry_run"] is True
    assert result["results"][0]["status"] == "dry_run"
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "proposed"


@pytest.mark.asyncio
async def test_gmail_folder_sort_keep_undo_returns_to_the_inbox(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "a@example.com")
    monkeypatch.delenv("CERID_INBOX_ACTIONS_ENABLED", raising=False)
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    _account(ledger, provider="gmail", folder_sort=True)
    decision_id = _propose(
        ledger,
        source="gmail",
        action="keep",
        category="actionable",
        provider_thread_id="g1",
    )
    transport = _gmail_transport()
    live = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert live["results"][0]["status"] == "applied"
    assert ledger.get_decision(decision_id)["status"] == "applied"
    moved = [
        call["arguments"] for call in transport.calls
        if call["tool"] == "batch_modify_gmail_message_labels"
    ]
    assert moved[-1]["remove_label_ids"] == ["INBOX"]
    assert ledger.update_account("gmail", "a@example.com", folder_sort=False)
    transport.calls.clear()
    undone = await undo_decision(decision_id, ledger=ledger, transport=transport)
    assert undone["status"] == "dry_run"
    assert transport.calls == []
    batch = _call_args(undone)[0]
    assert batch["add_label_ids"] == ["INBOX"]
    assert batch["remove_label_ids"] == [LABEL_NAME["actionable"]]
    assert ledger.open_proposal("gmail", "g1") is None


@pytest.mark.asyncio
async def test_outlook_folder_sort_keep_undo_returns_to_inbox(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, folder_sort=True)
    decision_id = _propose(ledger, action="keep", category="actionable", provider_thread_id="o1")
    planned = await apply_ids([decision_id], ledger=ledger, transport=RecordingTransport())
    destinations = [
        call["arguments"]["body"]["destinationId"]
        for call in planned["results"][0]["calls"]
        if call["tool"] == "move-mail-message"
    ]
    assert destinations == [LABEL_NAME["actionable"]]
    transport = ScriptedTransport({
        "update-mail-message": '{"id": "m1"}',
        "list-mail-folders": '{"value": [{"id": "p", "displayName": "Cerid"}]}',
        "list-mail-child-folders": '{"value": [{"id": "c", "displayName": "Action"}]}',
        "move-mail-message": '{"id": "moved-1"}',
    })
    live = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert live["results"][0]["status"] == "applied"
    ledger.update_account("outlook", "a@example.com", folder_sort=False)
    undone = await undo_decision(decision_id, ledger=ledger, transport=RecordingTransport())
    assert undone["status"] == "dry_run"
    move = [call for call in undone["calls"] if call["tool"] == "move-mail-message"]
    assert move[-1]["arguments"]["body"]["destinationId"] == "inbox"


@pytest.mark.asyncio
async def test_apple_folder_sort_keep_undo_returns_to_the_recorded_mailbox(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="apple_mail", folder_sort=True)
    decision_id = _propose(
        ledger,
        source="apple_mail",
        action="keep",
        category="actionable",
        provider_thread_id="a1",
        mailbox_before="Projects",
    )
    planned = await apply_ids([decision_id], ledger=ledger, transport=RecordingTransport())
    argv = [call["arguments"]["argv"] for call in planned["results"][0]["calls"]]
    assert ["move", "m1", LABEL_NAME["actionable"]] in argv
    ledger.record(decision_id, status="applied", receipt={})
    ledger.update_account("apple_mail", "a@example.com", folder_sort=False)
    undone = await undo_decision(decision_id, ledger=ledger, transport=RecordingTransport())
    assert undone["status"] == "dry_run"
    assert undone["calls"][-1]["arguments"]["argv"] == ["move", "m1", "Projects"]


@pytest.mark.asyncio
async def test_auto_apply_cap_skips_ineligible_rows(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, auto_apply=["newsletter"])
    _propose(ledger, action="draft", provider_thread_id="draft", category="newsletter")
    ids = [
        _propose(ledger, provider_thread_id=f"n{index}", message_ids=[f"m{index}"])
        for index in range(4)
    ]
    transport = RecordingTransport()
    result = await auto_apply_pass(ledger, transport=transport, cap=2, actions_enabled=True)
    assert result["applied"] == 2
    assert len(transport.calls) == 2
    assert ledger.get_decision(ids[0])["status"] == "applied"
    assert ledger.get_decision(ids[1])["status"] == "applied"
    assert ledger.get_decision(ids[2])["status"] == "proposed"
    assert ledger.get_decision(ids[3])["status"] == "proposed"
    assert ledger.open_proposal("outlook", "draft")["status"] == "proposed"


@pytest.mark.asyncio
async def test_auto_apply_cap_counts_attempts_not_successes(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, auto_apply=["newsletter"])
    ids = [
        _propose(ledger, provider_thread_id=f"n{index}", message_ids=[f"m{index}"])
        for index in range(3)
    ]

    class FailingTransport(RecordingTransport):
        async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
            self.calls.append({"provider": provider, "tool": tool, "arguments": arguments})
            raise RuntimeError("graph 503")

    transport = FailingTransport()
    result = await auto_apply_pass(ledger, transport=transport, cap=2, actions_enabled=True)
    assert result["applied"] == 0
    assert result["attempted"] == 2
    assert len(transport.calls) == 2
    assert ledger.get_decision(ids[0])["status"] == "failed"
    assert ledger.get_decision(ids[1])["status"] == "failed"
    assert ledger.get_decision(ids[2])["status"] == "proposed"


@pytest.mark.asyncio
async def test_excluded_and_removed_accounts_are_not_auto_applied(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, included=False, auto_apply=["newsletter"])
    excluded = _propose(ledger, provider_thread_id="ex")
    _account(ledger, address="gone@example.com", auto_apply=["newsletter"])
    ledger.remove_account("outlook", "gone@example.com")
    removed = _propose(ledger, provider_thread_id="gone", account_address="gone@example.com")
    transport = RecordingTransport()
    result = await auto_apply_pass(ledger, transport=transport, actions_enabled=True)
    assert result["applied"] == 0
    assert transport.calls == []
    assert ledger.get_decision(excluded)["status"] == "proposed"
    assert ledger.get_decision(removed)["status"] == "proposed"


@pytest.mark.asyncio
async def test_removed_account_can_still_undo(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, folder_sort=True)
    decision_id = _propose(ledger, action="keep", category="actionable", provider_thread_id="back")
    ledger.record(decision_id, status="applied", receipt={})
    assert ledger.remove_account("outlook", "a@example.com")
    undone = await undo_decision(decision_id, ledger=ledger, transport=RecordingTransport())
    assert undone["status"] == "dry_run"
    assert undone["calls"]
    assert ledger.get_account("outlook", "a@example.com")["removed"] is True


@pytest.mark.asyncio
async def test_second_gmail_address_is_pending_and_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "a@example.com")
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    added = add_account(ledger, provider="gmail", address="other@example.com")
    assert added["consent"] == "pending"
    decision_id = _propose(
        ledger,
        source="gmail",
        account_address="other@example.com",
        provider_thread_id="other",
    )
    transport = RecordingTransport()
    result = await apply_ids([decision_id], ledger=ledger, transport=transport, actions_enabled=True)
    assert result["results"][0]["status"] == "skipped"
    assert result["results"][0]["reason"] == "pending_account"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_second_apply_of_the_same_row_does_not_call_transport(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    first = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    second = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert first["results"][0]["status"] == "applied"
    assert second["results"][0]["status"] == "skipped"
    assert second["results"][0]["reason"] == "not proposed"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_actions_flag_off_proposes_and_applies_nothing(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="gmail", auto_apply=["newsletter"])
    result = await record_and_apply(type("R", (), {"threads": [_thread()]})(), ledger=ledger)
    assert result["proposed"] == 1
    assert result["applied"] == 0
    assert result["reason"] == "actions flag is off"
    assert ledger.list_decisions(status="proposed")[0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_live_undo_writes_its_own_row_and_marks_the_original(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    decision_id = _propose(ledger, provider_thread_id="live")
    transport = RecordingTransport()
    await apply_ids([decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True)
    undone = await undo_decision(
        decision_id, dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert undone["status"] == "applied"
    assert ledger.get_decision(decision_id)["status"] == "undone"
    assert ledger.get_decision(undone["undo_id"])["action"] == "undo"
    assert ledger.get_decision(undone["undo_id"])["status"] == "applied"


@pytest.mark.asyncio
async def test_live_undo_with_the_flag_off_adds_no_row(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    decision_id = _propose(ledger, provider_thread_id="live")
    transport = RecordingTransport()
    await apply_ids([decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True)
    reproposed = _propose(ledger, provider_thread_id="live")
    rows_before = len(ledger.list_decisions())
    calls_before = len(transport.calls)
    undone = await undo_decision(
        decision_id, dry_run=False, ledger=ledger, transport=transport, actions_enabled=False,
    )
    assert undone["status"] == "disabled"
    assert undone["calls"]
    assert "undo_id" not in undone
    assert len(ledger.list_decisions()) == rows_before
    assert len(transport.calls) == calls_before
    assert ledger.get_decision(decision_id)["status"] == "applied"
    assert ledger.get_decision(reproposed)["status"] == "proposed"


def test_propose_skips_subject_only_groups_and_unknown_accounts(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="gmail")
    count = propose_triage(ledger, [
        _thread(writable=False, thread_id="subject"),
        _thread(message_ids=[], thread_id="empty"),
        _thread(account="missing@example.com", thread_id="missing"),
        _thread(action="review", thread_id="word"),
        _thread(thread_id="bill", artifact_id="in1", finance_artifact_id="fin1", utility="financial"),
    ])
    assert count == 2
    filed = {row["provider_thread_id"]: row for row in ledger.list_decisions(status="proposed")}
    assert filed["word"]["action"] == "keep"
    assert filed["bill"]["rag_artifact_ids"] == [
        {"id": "in1", "domain": "inbox"},
        {"id": "fin1", "domain": "inbox"},  # the card is an inbox row of its own record type
    ]


def test_one_included_account_fills_a_thread_that_has_no_address(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="outlook")
    assert propose_triage(ledger, [_thread(source="outlook", account="", thread_id="only")]) == 1
    _account(ledger, provider="apple_mail", address="one@example.com")
    _account(ledger, provider="apple_mail", address="two@example.com")
    assert propose_triage(ledger, [_thread(source="apple_mail", account="", thread_id="ambiguous")]) == 0


def test_skip_leaves_the_mailbox_alone(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    decision_id = _propose(ledger)
    assert skip_decision(ledger, decision_id)["status"] == "skipped"
    assert ledger.get_decision(decision_id)["status"] == "skipped"
    assert skip_decision(ledger, decision_id)["reason"] == "not proposed"


def test_readding_a_removed_account_keeps_folder_sort(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="gmail", folder_sort=True)
    ledger.remove_account("gmail", "a@example.com")
    restored = add_account(ledger, provider="gmail", address="a@example.com")
    assert restored["removed"] is False
    assert restored["included"] is True
    assert restored["folder_sort"] is True


def test_utilities_is_not_an_account_setting(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    assert change_account(ledger, "outlook", "a@example.com", utilities=["none"]) is None
    assert "utilities" not in ledger.get_account("outlook", "a@example.com")


@pytest.mark.asyncio
async def test_noop_already_labeled_mail_is_recorded_applied(tmp_path: Path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, provider="gmail", auto_apply=["newsletter"])
    decision_id = _propose(
        ledger,
        source="gmail",
        action="archive",
        category="newsletter",
        provider_thread_id="done",
        current_labels=[LABEL_NAME["newsletter"]],
    )
    transport = RecordingTransport()
    result = await auto_apply_pass(ledger, transport=transport, actions_enabled=True)
    assert result["applied"] == 1
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "applied"


def test_discovered_addresses_do_not_scan_apple_until_asked(monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "a@example.com")
    calls: list[int] = []

    def _apple() -> list[str]:
        calls.append(1)
        return ["mac@example.com"]

    quiet = discovered_addresses(apple=_apple)
    assert quiet == {
        "gmail": ["a@example.com"],
        "outlook": [],
        "apple_mail": [],
        "error": "",
    }
    assert calls == []
    loud = discovered_addresses(discover_apple=True, apple=_apple)
    assert loud["apple_mail"] == ["mac@example.com"]
    assert calls == [1]


def test_setup_view_reports_the_source_state(tmp_path: Path, monkeypatch):
    from unittest.mock import MagicMock

    from app.inbox.review import setup_view

    ledger = _ledger(tmp_path, monkeypatch)
    desktop = MagicMock(spec=["is_configured", "configured_state"])
    desktop.is_configured.return_value = False
    desktop.configured_state.return_value = "runs_on_desktop"
    plain = MagicMock(spec=["is_configured"])
    plain.is_configured.return_value = True
    sources = {"apple_mail": desktop, "gmail": plain}
    with patch("app.data_sources.registry.get", side_effect=sources.get):
        assert setup_view(ledger, "apple_mail")["source_state"] == "runs_on_desktop"
        assert setup_view(ledger, "gmail")["source_state"] == "configured"
        assert setup_view(ledger, "outlook")["source_state"] == "not_registered"
        assert setup_view(ledger)["source_state"] == ""


@pytest.mark.asyncio
async def test_apple_discovery_off_darwin_names_the_desktop(monkeypatch):
    from app.inbox.review import scan_apple_addresses

    with (
        patch("platform.system", return_value="Linux"),
        patch("asyncio.create_subprocess_exec", side_effect=AssertionError("no helper runs here")),
    ):
        found = await scan_apple_addresses()
    assert found == {"addresses": [], "error": "runs_on_desktop"}


def test_open_ledger_uses_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ledger = open_ledger()
    assert ledger.path == tmp_path / "inbox.sqlite"
    assert ledger.path.exists()


def test_apply_fields_copies_provider_metadata():
    thread = _thread(message_ids=[], account="", mailbox="")

    class _Message:
        def __init__(self, metadata: dict) -> None:
            self.metadata = metadata

    _apply_fields(thread, [
        _Message({"provider_message_id": "m1", "account": "a@example.com", "mailbox": "Projects"}),
        _Message({"provider_message_id": "m1"}),
        _Message({"provider_message_id": "m2"}),
    ])
    assert thread.message_ids == ["m1", "m2"]
    assert thread.account == "a@example.com"
    assert thread.mailbox == "Projects"


@pytest.mark.asyncio
async def test_mcp_tools_are_gated_before_the_ledger(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with patch("config.features.is_feature_enabled", return_value=False):
        applied = await pkb_inbox_apply("d1,d2")
        undone = await pkb_inbox_undo("d1")
    assert applied["status"] == "feature_gated"
    assert undone["status"] == "feature_gated"
    assert not (tmp_path / "inbox.sqlite").exists()


def test_rest_feature_gate_does_not_open_the_ledger(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.routers import inbox_setup

    app = FastAPI()
    app.include_router(inbox_setup.router)
    with patch("config.features.is_feature_enabled", return_value=False):
        client = TestClient(app)
        setup = client.get("/inbox/setup")
        apply = client.post("/inbox/apply", json={"decision_ids": ["d1"]})
    assert setup.status_code == 403
    assert apply.status_code == 403
    assert not (tmp_path / "inbox.sqlite").exists()


def test_rest_maps_account_errors(monkeypatch, tmp_path: Path):
    ledger = _ledger(tmp_path, monkeypatch)
    from app.routers import inbox_setup

    monkeypatch.setattr(inbox_setup, "get_ledger", lambda: ledger)
    app = FastAPI()
    app.include_router(inbox_setup.router)
    with patch("config.features.is_feature_enabled", return_value=True):
        client = TestClient(app)
        bad = client.post("/inbox/accounts", json={"provider": "imap", "address": "a@example.com"})
        missing = client.post("/inbox/accounts/remove", json={"provider": "gmail", "address": "a@example.com"})
        added = client.post("/inbox/accounts", json={"provider": "outlook", "address": "a@example.com"})
        rejected = client.patch(
            "/inbox/accounts",
            json={"provider": "outlook", "address": "a@example.com", "utilities": ["none"]},
        )
        view = client.get("/inbox/setup", params={"provider": "outlook"})
    assert bad.status_code == 400
    assert missing.status_code == 404
    assert added.status_code == 200
    assert rejected.status_code == 400
    assert view.status_code == 200
    assert view.json()["accounts"][0]["address"] == "a@example.com"
    assert view.json()["pins"] == []


def test_a_proposal_keeps_the_classification_reason(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.upsert_account(provider="gmail", address="a@example.com")
    thread = TriagedThread(
        thread_id="thread-9",
        source="gmail",
        participants=["ada@example.com"],
        subject="Hello",
        message_count=1,
        latest_at="0",
        category="promo",
        summary="sale",
        suggested_action="archive",
        action="archive",
        writable=True,
        message_ids=["m1"],
        account="a@example.com",
        classification_reason="phrase:sale",
    )
    assert propose_triage(ledger, [thread]) == 1
    listed = ledger.list_decisions(status="proposed")
    assert listed[0]["classification_reason"] == "phrase:sale"


def test_morning_brief_review_is_not_urgent():
    from core.agents.inbox_review import review_message

    decision = review_message(
        sender="briefs@example.com",
        subject="Morning brief",
        body="critical path in the notes",
    )
    assert decision["category"] != "urgent"
    assert decision["action"] == "keep"
    assert decision["sticks"] is False


def test_review_sale_sticks_and_a_reply_does_not_archive():
    from core.agents.inbox_review import render_record, review_message

    sale = review_message(sender="ada@shop.example", subject="Sale this week", body="come by the shop")
    assert sale["category"] == "promo"
    assert sale["action"] == "archive"
    assert sale["sticks"] is True
    assert sale["band"] == "skip"
    assert sale["reason"] == "phrase:sale"
    reply = review_message(
        sender="ada@example.com",
        subject="Re: dinner",
        body="thanks for the note",
        headers={"in_reply_to": "<prev@example.com>"},
    )
    assert reply["category"] == "personal"
    assert reply["action"] == "keep"
    assert reply["sticks"] is False
    won = review_message(sender="prize@example.com", subject="URGENT", body="you've won a trip")
    assert won["category"] == "spam"
    assert won["reason"] == "phrase:you've won"
    bill = review_message(
        sender="billing@power.example",
        subject="City Power",
        body="Your bill is ready.",
    )
    assert bill["utility"] == "financial"
    assert bill["financial_marker"] == "your bill"
    traced = review_message(
        sender="ada@example.com",
        subject="Hello",
        body="hello",
        rspamd={
            "action": "add header",
            "score": 6.5,
            "symbols": {
                "MANY_INVISIBLE_PARTS": {"score": 1.5, "options": ["http://secret.example/x"]},
                "MIME_GOOD": -0.1,
            },
        },
    )
    assert traced["rspamd_action"] == "add header"
    assert traced["symbols"][0][0] == "MANY_INVISIBLE_PARTS"
    assert all("http" not in name for name, _score in traced["symbols"])
    line = render_record({
        "mailbox": "INBOX",
        "from": "ada@shop.example",
        "to": "me@icloud.com",
        "subject": "Sale this week",
        "body": "come by the shop",
    })
    assert line is not None
    assert "ada@" not in line
    assert "shop.example" in line
    assert "phrase:sale" in line


def test_review_redacts_nothing_when_the_setting_is_empty(monkeypatch):
    from core.agents.inbox_review import is_sensitive, render_record

    monkeypatch.delenv("CERID_INBOX_REVIEW_REDACT", raising=False)
    assert is_sensitive("x@mail.example.org", "me@contoso.com", "Re: contoso renewal") is False
    line = render_record({
        "from": "person@example.com",
        "to": "analyst@mail.example.org",
        "subject": "contoso",
        "body": "hello",
    })
    assert line is not None
    assert "mail.example.org" in line


def test_review_redacts_from_and_subject_matches_from_the_setting(monkeypatch):
    from core.agents.inbox_review import is_sensitive, render_record

    monkeypatch.setenv("CERID_INBOX_REVIEW_REDACT", "example.org, Contoso")
    assert is_sensitive("x@mail.example.org", "me@icloud.com", "Hello") is True
    assert is_sensitive("a@example.com", "b@example.com", "Re: contoso renewal") is True
    assert is_sensitive("a@example.com", "b@example.com", "Hello") is False
    hidden = render_record({
        "from": "x@mail.example.org",
        "to": "me@icloud.com",
        "subject": "Hello",
        "body": "hello",
    })
    assert hidden is None


def test_review_module_source_carries_no_literal_domain():
    import ast
    import re

    import core.agents.inbox_review as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "_SENSITIVE" not in source
    domain = re.compile(r"[A-Za-z0-9-]+\.(?:gov|com|org|net|mil|edu|io)\b")
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert not [text for text in literals if domain.search(text)]


@pytest.mark.asyncio
async def test_gmail_mark_read_undo_restores_unread(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "a@example.com")
    monkeypatch.setenv("CERID_INBOX_ACTIONS_ENABLED", "false")
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    _account(ledger, provider="gmail")
    decision_id = _propose(
        ledger, source="gmail", action="mark_read", category="actionable", provider_thread_id="g2",
    )
    transport = _gmail_transport()
    live = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert live["results"][0]["status"] == "applied"
    batches = [
        call["arguments"] for call in transport.calls
        if call["tool"] == "batch_modify_gmail_message_labels"
    ]
    assert batches[-1]["remove_label_ids"] == ["UNREAD"]
    assert "add_label_ids" not in batches[-1]
    undone = await undo_decision(decision_id, ledger=ledger, transport=transport)
    assert undone["status"] == "dry_run"
    batch = _call_args(undone)[0]
    assert batch["add_label_ids"] == ["UNREAD"]
    assert "remove_label_ids" not in batch


@pytest.mark.asyncio
async def test_outlook_undo_keeps_the_operators_own_categories(tmp_path: Path, monkeypatch):
    import json

    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger)
    thread = _thread(
        source="outlook",
        thread_id="o3",
        action="keep",
        category="actionable",
        observation={"categories": "Blue category"},
    )
    assert propose_triage(ledger, [thread]) == 1
    decision_id = ledger.open_proposal("outlook", "o3")["id"]
    transport = ScriptedTransport({"update-mail-message": '{"id": "m1"}'})
    live = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert live["results"][0]["status"] == "applied"
    assert transport.calls[0]["arguments"]["body"]["categories"] == ["Blue category", "Cerid/Action"]
    receipt = json.loads(ledger.get_decision(decision_id)["receipt_json"])
    assert receipt["current_labels"] == ["Blue category", "Cerid/Action"]
    undone = await undo_decision(decision_id, ledger=ledger, transport=transport)
    assert undone["status"] == "dry_run"
    assert undone["calls"][0]["tool"] == "update-mail-message"
    assert undone["calls"][0]["arguments"]["body"]["categories"] == ["Blue category"]
    # The keep did not move the message, so the undo has nothing to move back.
    assert [call["tool"] for call in undone["calls"]] == ["update-mail-message"]


def test_apply_fields_reads_the_outlook_folder_as_the_mailbox():
    thread = _thread(source="outlook", message_ids=[], mailbox="")

    class _Message:
        def __init__(self, metadata: dict) -> None:
            self.metadata = metadata

    _apply_fields(thread, [_Message({"provider_message_id": "m1", "folder": "inbox"})])
    assert thread.mailbox == "inbox"


@pytest.mark.asyncio
async def test_outlook_undo_targets_the_moved_id_and_the_prior_folder(tmp_path: Path, monkeypatch):
    import json

    ledger = _ledger(tmp_path, monkeypatch)
    _account(ledger, folder_sort=True)
    thread = _thread(source="outlook", thread_id="o4", action="keep", category="actionable", mailbox="inbox")
    assert propose_triage(ledger, [thread]) == 1
    decision_id = ledger.open_proposal("outlook", "o4")["id"]
    transport = ScriptedTransport({
        "update-mail-message": '{"id": "m1"}',
        "list-mail-folders": '{"value": [{"id": "p", "displayName": "Cerid"}]}',
        "list-mail-child-folders": '{"value": [{"id": "c", "displayName": "Action"}]}',
        "move-mail-message": '{"id": "moved-1", "parentFolderId": "c"}',
    })
    live = await apply_ids(
        [decision_id], dry_run=False, ledger=ledger, transport=transport, actions_enabled=True,
    )
    assert live["results"][0]["status"] == "applied"
    receipt = json.loads(ledger.get_decision(decision_id)["receipt_json"])
    assert receipt["message_ids"] == ["moved-1"]
    ledger.update_account("outlook", "a@example.com", folder_sort=False)
    undone = await undo_decision(decision_id, ledger=ledger, transport=transport)
    assert undone["status"] == "dry_run"
    assert [call["arguments"]["messageId"] for call in undone["calls"]] == ["moved-1", "moved-1"]
    assert undone["calls"][0]["arguments"]["body"]["categories"] == []
    assert undone["calls"][1]["arguments"]["body"]["destinationId"] == "inbox"
