# src/mcp/tests/test_read_after_write.py
from unittest.mock import MagicMock


def test_returns_true_when_record_has_truthy_exists():
    from core.reliability.read_after_write import assert_created
    session = MagicMock()
    record = MagicMock()
    record.get.return_value = True
    session.run.return_value.single.return_value = record

    ok = assert_created(
        session,
        check_cypher="MATCH ... RETURN true AS exists",
        params={},
        event_name="test.ok",
    )
    assert ok is True


def test_returns_false_when_record_is_none(monkeypatch):
    from core.reliability.read_after_write import assert_created
    captures = []
    monkeypatch.setattr(
        "sentry_sdk.capture_message",
        lambda *a, **k: captures.append((a, k)),
    )
    session = MagicMock()
    session.run.return_value.single.return_value = None

    ok = assert_created(
        session,
        check_cypher="MATCH ... RETURN 1",
        params={"cid": "abc"},
        event_name="test.missing",
    )
    assert ok is False
    assert len(captures) == 1
    assert "test.missing" in captures[0][0][0]


def test_returns_false_when_record_missing_key():
    from core.reliability.read_after_write import assert_created
    session = MagicMock()
    record = MagicMock()
    record.get.return_value = False
    session.run.return_value.single.return_value = record
    ok = assert_created(
        session,
        check_cypher="MATCH ... RETURN false AS exists",
        params={},
        event_name="test.false",
    )
    assert ok is False


def test_returns_false_when_session_raises(monkeypatch):
    from core.reliability.read_after_write import assert_created
    captures = []
    monkeypatch.setattr(
        "sentry_sdk.capture_exception",
        lambda *a, **k: captures.append((a, k)),
    )
    session = MagicMock()
    session.run.side_effect = RuntimeError("driver down")
    ok = assert_created(
        session,
        check_cypher="MATCH ... RETURN 1",
        params={},
        event_name="test.raised",
    )
    assert ok is False
    assert len(captures) == 1


def test_custom_boolean_key():
    from core.reliability.read_after_write import assert_created
    session = MagicMock()
    record = MagicMock()
    record.get.side_effect = lambda k, default: {"landed": True}.get(k, default)
    session.run.return_value.single.return_value = record
    ok = assert_created(
        session,
        check_cypher="RETURN true AS landed",
        params={},
        event_name="test.custom",
        boolean_key="landed",
    )
    assert ok is True
