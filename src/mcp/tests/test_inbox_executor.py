# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Observe executors. Fakes stand in for the sibling and for ceridmail.

Dry-run is the default. A live write still does nothing until
CERID_INBOX_ACTIONS_ENABLED is on. No test starts Mail or a sibling.
"""
from __future__ import annotations

from pathlib import Path

from app.inbox.executor import apply_decision
from app.inbox.ledger import InboxLedger
from core.agents.inbox_actions import (
    APPLE_COMMANDS,
    FLAG_COLOR,
    FORBIDDEN_TOOLS,
    GMAIL_TOOLS,
    LABEL_NAME,
    OUTLOOK_TOOLS,
    PROVIDER_TOOLS,
)
from core.agents.inbox_apply import plan_apply

_REPO = Path(__file__).resolve().parents[3]
_MCP = Path(__file__).resolve().parents[1]


class RecordingTransport:
    """Boundary fake. Records every call and returns a harmless body."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
        self.calls.append({"provider": provider, "tool": tool, "arguments": arguments})
        return ""

    async def apple(self, argv: list[str]) -> tuple[int, dict]:
        self.calls.append({"provider": "apple_mail", "tool": argv[0], "arguments": {"argv": list(argv)}})
        return 0, {"ok": True}


class ScriptedTransport(RecordingTransport):
    """Sibling replies shaped like the pinned servers, keyed by tool name."""

    def __init__(self, tools: dict[str, object], apple: tuple[int, dict] = (0, {"ok": True})) -> None:
        super().__init__()
        self._tools = tools
        self._apple = apple

    async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
        self.calls.append({"provider": provider, "tool": tool, "arguments": arguments})
        reply = self._tools[tool]
        if callable(reply):
            return str(reply(arguments))
        return str(reply)

    async def apple(self, argv: list[str]) -> tuple[int, dict]:
        self.calls.append({"provider": "apple_mail", "tool": argv[0], "arguments": {"argv": list(argv)}})
        return self._apple


def _decision(**overrides: object) -> dict:
    decision = {
        "decision_id": "d1",
        "provider": "gmail",
        "action": "archive",
        "category": "newsletter",
        "utility": "none",
        "account": "a@example.com",
        "account_known": True,
        "account_included": True,
        "folder_sort": False,
        "message_ids": ["m1"],
        "provider_thread_id": "t1",
        "current_labels": ["INBOX"],
    }
    decision.update(overrides)
    return decision


def _batch(result: dict) -> dict:
    calls = [call for call in result["calls"] if call["tool"] == "batch_modify_gmail_message_labels"]
    assert len(calls) == 1
    return calls[0]["arguments"]


def _propose(ledger: InboxLedger, **overrides: object) -> str:
    fields = {
        "account_address": "a@example.com",
        "source": "gmail",
        "provider_thread_id": "t1",
        "category": "newsletter",
        "utility": "none",
        "action": "archive",
        "band": "local-small",
    }
    fields.update(overrides)
    return ledger.propose(**fields)  # type: ignore[arg-type]


async def test_gmail_archive_adds_one_cerid_label_and_removes_inbox():
    result = await apply_decision(_decision())
    assert result["status"] == "dry_run"
    arguments = _batch(result)
    assert arguments["add_label_ids"] == [LABEL_NAME["newsletter"]]
    assert arguments["add_label_ids"] == ["Cerid/Newsletter"]
    assert arguments["remove_label_ids"] == ["INBOX"]
    assert arguments["user_google_email"] == "a@example.com"
    assert arguments["message_ids"] == ["m1"]


async def test_gmail_undo_restores_inbox_and_drops_the_cerid_label():
    result = await apply_decision(_decision(action="undo", current_labels=["Cerid/Newsletter"]))
    arguments = _batch(result)
    assert arguments["add_label_ids"] == ["INBOX"]
    assert arguments["remove_label_ids"] == ["Cerid/Newsletter"]


async def test_gmail_second_archive_is_a_noop(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id, current_labels=["Cerid/Newsletter"]),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "noop"
    assert result["calls"] == []
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "proposed"


async def test_outlook_keep_does_not_move_until_folder_sort_is_on():
    held = await apply_decision(_decision(
        provider="outlook",
        action="keep",
        category="actionable",
        folder_sort=False,
        current_labels=[],
    ))
    assert [call["tool"] for call in held["calls"]] == ["update-mail-message"]
    assert held["calls"][0]["arguments"]["body"]["categories"] == ["Cerid/Action"]
    assert "destinationId" not in held["calls"][0]["arguments"]["body"]

    filed = await apply_decision(_decision(
        provider="outlook",
        action="keep",
        category="actionable",
        folder_sort=True,
        current_labels=[],
    ))
    assert [call["tool"] for call in filed["calls"]] == ["update-mail-message", "move-mail-message"]
    assert filed["calls"][1]["arguments"]["body"]["destinationId"] == "Cerid/Action"
    assert filed["calls"][1]["arguments"]["messageId"] == "m1"


async def test_outlook_archive_and_undo_use_well_known_folders():
    archived = await apply_decision(_decision(provider="outlook", action="archive", category="newsletter"))
    assert archived["calls"][-1]["tool"] == "move-mail-message"
    assert archived["calls"][-1]["arguments"]["body"]["destinationId"] == "archive"
    undone = await apply_decision(_decision(
        provider="outlook",
        action="undo",
        category="newsletter",
        current_labels=["Cerid/Newsletter"],
        mailbox_before="inbox",
    ))
    assert undone["calls"][0]["arguments"]["body"]["categories"] == []
    assert undone["calls"][1]["arguments"]["body"]["destinationId"] == "inbox"


async def test_outlook_live_keep_resolves_the_cerid_folder_before_the_move():
    def _created(arguments: dict) -> str:
        name = arguments["body"]["displayName"]
        folder_id = "parent-1" if name == "Cerid" else "child-1"
        return f'{{"id": "{folder_id}", "displayName": "{name}"}}'

    transport = ScriptedTransport({
        "update-mail-message": '{"id": "m1"}',
        "list-mail-folders": '{"value": []}',
        "create-mail-folder": _created,
        "list-mail-child-folders": '{"value": []}',
        "create-mail-child-folder": _created,
        "move-mail-message": '{"id": "moved-1"}',
    })
    result = await apply_decision(
        _decision(provider="outlook", action="keep", category="actionable", folder_sort=True),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "applied"
    assert [call["tool"] for call in transport.calls] == [
        "update-mail-message",
        "list-mail-folders",
        "create-mail-folder",
        "list-mail-child-folders",
        "create-mail-child-folder",
        "move-mail-message",
    ]
    move = transport.calls[-1]["arguments"]
    assert move["body"]["destinationId"] == "child-1"
    assert transport.calls[2]["arguments"]["body"]["displayName"] == "Cerid"
    assert transport.calls[4]["arguments"] == {
        "mailFolderId": "parent-1",
        "body": {"displayName": "Action"},
    }


async def test_outlook_live_archive_uses_the_well_known_name():
    transport = ScriptedTransport({
        "update-mail-message": '{"id": "m1"}',
        "move-mail-message": '{"id": "moved-1"}',
    })
    result = await apply_decision(
        _decision(provider="outlook", action="archive"),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "applied"
    assert [call["tool"] for call in transport.calls] == ["update-mail-message", "move-mail-message"]
    assert transport.calls[-1]["arguments"]["body"]["destinationId"] == "archive"


def test_apple_action_builds_argv():
    message_id = "<abc@x>"
    keep = plan_apply(_decision(
        provider="apple_mail", action="keep", category="urgent", message_ids=[message_id],
    ))
    assert [call.arguments["argv"] for call in keep.calls] == [
        ["flag", message_id, "red"],
    ]
    assert FLAG_COLOR["urgent"] == "red"

    filed = plan_apply(_decision(
        provider="apple_mail",
        action="keep",
        category="urgent",
        folder_sort=True,
        message_ids=[message_id],
    ))
    assert [call.arguments["argv"] for call in filed.calls] == [
        ["flag", message_id, "red"],
        ["move", message_id, "Cerid/Urgent"],
    ]

    archived = plan_apply(_decision(
        provider="apple_mail", action="archive", category="newsletter", message_ids=[message_id],
    ))
    assert archived.calls[0].arguments["argv"] == ["move", message_id, "Archive"]

    read = plan_apply(_decision(
        provider="apple_mail", action="mark_read", category="personal", message_ids=[message_id],
    ))
    assert read.calls[0].arguments["argv"] == ["read", message_id]

    draft = plan_apply(_decision(
        provider="apple_mail",
        action="draft",
        category="actionable",
        message_ids=[message_id],
        subject="Re: Hello",
        draft_body="Thanks",
    ))
    assert draft.calls[0].arguments["argv"] == ["draft", message_id, "Re: Hello", "Thanks"]

    undo = plan_apply(_decision(
        provider="apple_mail",
        action="undo",
        category="personal",
        message_ids=[message_id],
        mailbox_before="Projects",
    ))
    assert undo.calls[0].arguments["argv"] == ["move", message_id, "Projects"]

    banned = plan_apply(_decision(
        provider="apple_mail", action="undo", category="personal", mailbox_before="Trash",
    ))
    assert banned.error
    assert banned.calls == ()

    provider_spam = plan_apply(_decision(
        provider="apple_mail", action="undo", category="spam", mailbox_before="Spam",
    ))
    assert provider_spam.error
    assert provider_spam.calls == ()


def test_spam_sorts_into_cerid_spam_and_not_the_provider_junk_mailbox():
    message_id = "<spam@x>"
    archived = plan_apply(_decision(
        provider="gmail",
        action="archive",
        category="spam",
        message_ids=[message_id],
        current_labels=["INBOX", "UNREAD"],
    ))
    arguments = archived.calls[0].arguments
    assert arguments["add_label_ids"] == ["Cerid/Spam"]
    assert arguments["remove_label_ids"] == ["INBOX"]

    filed = plan_apply(_decision(
        provider="apple_mail",
        action="keep",
        category="spam",
        folder_sort=True,
        message_ids=[message_id],
    ))
    assert [call.arguments["argv"] for call in filed.calls] == [
        ["flag", message_id, "gray"],
        ["move", message_id, "Cerid/Spam"],
    ]
    outlook = plan_apply(_decision(
        provider="outlook",
        action="keep",
        category="spam",
        folder_sort=True,
        message_ids=[message_id],
    ))
    assert outlook.calls[-1].arguments["body"]["destinationId"] == "Cerid/Spam"


async def test_apple_mail_not_running_queues_the_command(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    transport = ScriptedTransport({}, apple=(75, {"ok": False, "error": "mail_not_running"}))
    result = await apply_decision(
        _decision(provider="apple_mail", action="keep", category="urgent"),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "queued"
    assert result["reason"] == "mail_not_running"
    rows = ledger.list_outbox()
    assert len(rows) == 1
    assert rows[0]["command"] == {
        "provider": "apple_mail",
        "commands": [["flag", "m1", "red"]],
    }
    # The mailbox lookup comes first and is what finds Mail closed.
    assert transport.calls[0]["arguments"]["argv"] == ["find", "m1"]


async def test_apple_automation_denied_does_not_queue(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    transport = ScriptedTransport({}, apple=(78, {"ok": False, "error": "automation_denied"}))
    result = await apply_decision(
        _decision(provider="apple_mail", action="keep", category="urgent"),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "failed"
    assert result["reason"] == "automation_denied"
    assert ledger.list_outbox() == []
    assert transport.calls


async def test_apply_path_stays_on_the_allowlist():
    emitted: set[tuple[str, str]] = set()
    for provider in ("gmail", "outlook", "apple_mail"):
        for action in ("keep", "archive", "mark_read", "draft", "undo"):
            for folder_sort in (False, True):
                category = "newsletter" if action == "archive" else "actionable"
                decision = _decision(
                    provider=provider,
                    action=action,
                    category=category,
                    folder_sort=folder_sort,
                    current_labels=["INBOX", "UNREAD", "Cerid/Promo"],
                    draft_body="Thanks for the note.",
                    subject="Hello",
                    mailbox_before="INBOX",
                )
                plan = plan_apply(decision)
                assert plan.error == "", (provider, action, folder_sort, plan.error)
                result = await apply_decision(decision)
                assert result["status"] in {"dry_run", "noop"}
                tools = [call.tool for call in plan.calls]
                tools.extend(item["tool"] for item in result["calls"])
                for tool in tools:
                    assert tool in PROVIDER_TOOLS[provider]
                    assert tool not in FORBIDDEN_TOOLS
                    emitted.add((provider, tool))
                assert "create-draft-email" not in tools
    assert ("gmail", "batch_modify_gmail_message_labels") in emitted
    assert ("gmail", "draft_gmail_message") in emitted
    assert ("outlook", "update-mail-message") in emitted
    assert ("outlook", "move-mail-message") in emitted
    assert ("outlook", "create-reply-draft") in emitted
    assert ("apple_mail", "flag") in emitted
    assert ("apple_mail", "move") in emitted
    assert ("apple_mail", "read") in emitted
    assert ("apple_mail", "draft") in emitted
    assert FORBIDDEN_TOOLS.isdisjoint(GMAIL_TOOLS | OUTLOOK_TOOLS | APPLE_COMMANDS)
    for relative in ("core/agents/inbox_apply.py", "app/inbox/executor.py"):
        text = (_MCP / relative).read_text()
        for name in FORBIDDEN_TOOLS:
            assert name not in text, relative


async def test_dry_run_default_does_not_call_transport(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CERID_INBOX_ACTIONS_ENABLED", "true")
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id),
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "dry_run"
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "proposed"


async def test_actions_flag_off_does_not_call_transport(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CERID_INBOX_ACTIONS_ENABLED", raising=False)
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id),
        dry_run=False,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "disabled"
    assert result["reason"] == "actions flag is off"
    assert result["calls"][0]["tool"] == "batch_modify_gmail_message_labels"
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "proposed"


async def test_gmail_live_apply_resolves_label_ids_and_records(tmp_path: Path):
    # v1.21.0 list_gmail_labels is prose. Create answers with Name and ID lines.
    def _created(arguments: dict) -> str:
        name = arguments["name"]
        return f"Label created successfully!\nName: {name}\nID: Label_9"

    transport = ScriptedTransport({
        "list_gmail_labels": "Labels:\n  • Old (ID: Label_1)\n",
        "manage_gmail_label": _created,
        "batch_modify_gmail_message_labels": "Labels updated.",
    })
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    result = await apply_decision(
        _decision(decision_id=decision_id),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "applied"
    assert [call["tool"] for call in transport.calls] == [
        "list_gmail_labels",
        "manage_gmail_label",
        "batch_modify_gmail_message_labels",
    ]
    for call in transport.calls:
        assert call["arguments"]["user_google_email"] == "a@example.com"
        assert call["tool"] in GMAIL_TOOLS
    batch = transport.calls[-1]["arguments"]
    assert batch["add_label_ids"] == ["Label_9"]
    assert batch["remove_label_ids"] == ["INBOX"]
    assert "verify" not in batch
    assert ledger.get_decision(decision_id)["status"] == "applied"


async def test_gmail_draft_fails_closed_when_the_reply_omits_the_thread():
    # v1.21.0 returns a draft id and does not echo the thread. That reply is a failure.
    transport = ScriptedTransport({
        "draft_gmail_message": "Draft created successfully!\nDraft ID: d1",
    })
    result = await apply_decision(
        _decision(
            action="draft",
            category="actionable",
            provider_thread_id="thread-9",
            draft_body="Thanks",
            subject="Re: Hello",
        ),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "failed"
    assert result["reason"] == "draft thread was not confirmed"
    sent = transport.calls[0]["arguments"]
    assert sent["thread_id"] == "thread-9"
    assert sent["include_signature"] is False
    assert sent["quote_original"] is False
    assert sent["body"] == "Thanks"


async def test_gmail_draft_succeeds_when_the_reply_contains_the_thread():
    transport = ScriptedTransport({
        "draft_gmail_message": "Draft created successfully!\nDraft ID: d1\nThread ID: thread-9",
    })
    result = await apply_decision(
        _decision(
            action="draft",
            category="actionable",
            provider_thread_id="thread-9",
            draft_body="Thanks",
            subject="Re: Hello",
        ),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "applied"
    assert transport.calls[0]["tool"] == "draft_gmail_message"


async def test_empty_draft_does_not_call_the_transport():
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(action="draft", category="actionable", draft_body="  "),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "failed"
    assert result["reason"] == "draft body is missing"
    assert transport.calls == []


async def test_pending_account_is_skipped():
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(account="other@example.com", configured_address="a@example.com"),
        dry_run=False,
        actions_enabled=True,
        transport=transport,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "pending_account"
    assert transport.calls == []

    same = await apply_decision(_decision(
        account="A@Example.com",
        configured_address="a@example.com",
    ))
    assert same["status"] == "dry_run"


async def test_left_inbox_stores_the_cursor_without_a_write(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id, in_inbox=False, sync_cursor="cursor-9"),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "dropped"
    assert result["reason"] == "left_inbox"
    assert transport.calls == []
    assert ledger.get_sync_cursor("gmail", "a@example.com") == "cursor-9"
    assert ledger.get_decision(decision_id)["status"] == "dropped"


async def test_actions_flag_off_leaves_the_ledger_alone_when_the_message_left_the_inbox(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.delenv("CERID_INBOX_ACTIONS_ENABLED", raising=False)
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger)
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id, in_inbox=False, sync_cursor="cursor-9"),
        dry_run=False,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "disabled"
    assert result["reason"] == "actions flag is off"
    assert transport.calls == []
    assert ledger.get_sync_cursor("gmail", "a@example.com") == ""
    assert ledger.get_decision(decision_id)["status"] == "proposed"


async def test_actions_flag_off_does_not_record_a_plan_failure(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger, action="draft", category="actionable")
    transport = RecordingTransport()
    result = await apply_decision(
        _decision(decision_id=decision_id, action="draft", category="actionable", draft_body="  "),
        dry_run=False,
        actions_enabled=False,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "disabled"
    assert transport.calls == []
    assert ledger.get_decision(decision_id)["status"] == "proposed"


async def test_gmail_data_source_dry_run_does_not_call_the_sibling(monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "a@example.com")
    from plugins.gmail.data_source import GmailDataSource

    source = GmailDataSource()
    called: list[str] = []

    async def _explode(tool: str, arguments: dict) -> str:
        called.append(tool)
        raise AssertionError(tool)

    monkeypatch.setattr(source, "_call_mcp", _explode)
    result = await source.apply(_decision(), dry_run=True)
    assert result["status"] == "dry_run"
    assert called == []
    assert _batch(result)["add_label_ids"] == ["Cerid/Newsletter"]
    assert _batch(result)["remove_label_ids"] == ["INBOX"]


async def test_outlook_and_apple_dry_run_do_not_call_their_transports(monkeypatch):
    from plugins.apple_mail.data_source import AppleMailDataSource
    from plugins.outlook.data_source import OutlookDataSource

    outlook = OutlookDataSource()
    apple = AppleMailDataSource()
    called: list[str] = []

    async def _outlook(tool: str, arguments: dict) -> str:
        called.append(tool)
        raise AssertionError(tool)

    async def _apple(argv: list[str]) -> tuple[int, dict]:
        called.append(argv[0])
        raise AssertionError(argv)

    monkeypatch.setattr(outlook, "_call_mcp", _outlook)
    monkeypatch.setattr(apple, "invoke", _apple)
    outlook_result = await outlook.apply(_decision(provider="outlook", action="keep", category="actionable"))
    apple_result = await apple.apply(_decision(provider="apple_mail", action="keep", category="urgent"))
    assert outlook_result["status"] == "dry_run"
    assert apple_result["calls"][0]["arguments"]["argv"][0] == "flag"
    assert called == []


def test_connector_commands_stay_read_only_until_the_flag():
    text = (_REPO / "stacks/connectors/docker-compose.yml").read_text()
    assert "--read-only" in text
    assert "--single-user" in text
    assert "gmail:drafts" in text
    assert "Mail.ReadWrite Calendars.Read" in text
    assert "gmail:send" not in text
    assert "Mail.Send" not in text
    assert text.count("--single-user") >= 2
    assert text.count("--read-only") >= 2


async def test_gmail_undo_of_mark_read_restores_unread():
    result = await apply_decision(_decision(
        action="undo",
        undone_action="mark_read",
        current_labels=["INBOX"],
    ))
    assert result["status"] == "dry_run"
    arguments = _batch(result)
    assert arguments["add_label_ids"] == ["UNREAD"]
    assert "remove_label_ids" not in arguments


async def test_outlook_keep_adds_the_cerid_category_and_keeps_the_others():
    kept = await apply_decision(_decision(
        provider="outlook",
        action="keep",
        category="actionable",
        current_labels=["Blue category", "Cerid/Promo"],
    ))
    body = kept["calls"][0]["arguments"]["body"]
    assert body["categories"] == ["Blue category", "Cerid/Action"]
    undone = await apply_decision(_decision(
        provider="outlook",
        action="undo",
        undone_action="keep",
        category="actionable",
        current_labels=["Blue category", "Cerid/Action"],
    ))
    assert undone["calls"][0]["arguments"]["body"]["categories"] == ["Blue category"]
    unread = await apply_decision(_decision(
        provider="outlook",
        action="undo",
        undone_action="mark_read",
        category="actionable",
        current_labels=[],
    ))
    assert unread["calls"][0]["arguments"]["body"] == {"isRead": False}


async def test_outlook_undo_moves_back_only_to_a_recorded_folder():
    back = await apply_decision(_decision(
        provider="outlook",
        action="undo",
        undone_action="archive",
        current_labels=["Cerid/Newsletter"],
        mailbox_before="inbox",
    ))
    assert [call["tool"] for call in back["calls"]] == ["update-mail-message", "move-mail-message"]
    assert back["calls"][1]["arguments"]["body"]["destinationId"] == "inbox"
    stays = await apply_decision(_decision(
        provider="outlook",
        action="undo",
        undone_action="keep",
        current_labels=["Cerid/Newsletter"],
        mailbox_before="",
    ))
    assert [call["tool"] for call in stays["calls"]] == ["update-mail-message"]


async def test_apple_records_the_mailbox_before_a_flag_only_keep(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    decision_id = _propose(ledger, source="apple_mail", action="keep", category="urgent")

    class _FindingTransport(RecordingTransport):
        async def apple(self, argv: list[str]) -> tuple[int, dict]:
            await super().apple(argv)
            if argv[0] == "find":
                return 0, {"ok": True, "mailbox": "Projects"}
            return 0, {"ok": True}

    transport = _FindingTransport()
    result = await apply_decision(
        _decision(
            decision_id=decision_id,
            provider="apple_mail",
            action="keep",
            category="urgent",
            folder_sort=False,
        ),
        dry_run=False,
        actions_enabled=True,
        ledger=ledger,
        transport=transport,
    )
    assert result["status"] == "applied"
    assert [call["tool"] for call in transport.calls] == ["find", "flag"]
    assert ledger.get_decision(decision_id)["mailbox_before"] == "Projects"
