"""``parse_llm_json`` must survive trailing content, not just truncation.

Regression guard for the 2026-07-29 agent audit: `/agent/memory/extract`
returned 0 memories on every call against the local provider. Two root causes —
a cloud-tuned timeout budget, and this one: local models routinely emit a valid
JSON value followed by commentary, which raises ``JSONDecodeError: Extra data``.
Recovery only handled truncated output, so the error propagated, the caller
swallowed it, and extraction silently produced nothing.
"""

import json

import pytest

from core.utils.llm_parsing import parse_llm_json


def test_plain_json_still_parses():
    assert parse_llm_json('[{"content": "a"}]') == [{"content": "a"}]


def test_fenced_json_still_parses():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("tail", [
    "\n\nThat's everything I found worth remembering.",
    "\nNote: extracted 1 item.",
    '\n{"content": "a second stray object"}',
    "\n```",
])
def test_trailing_content_after_valid_json_recovers(tail):
    """A complete value followed by anything must yield the value."""
    payload = '[{"content": "oat milk", "memory_type": "preference"}]'
    assert parse_llm_json(payload + tail) == [
        {"content": "oat milk", "memory_type": "preference"}
    ]


def test_trailing_content_after_object_recovers():
    assert parse_llm_json('{"a": 1}\nDone.') == {"a": 1}


@pytest.mark.parametrize("truncated,expected", [
    ('[{"content": "abc"}, {"conte', [{"content": "abc"}, {}]),
    ('[{"content": "abc"},', [{"content": "abc"}]),
])
def test_truncated_json_recovery_still_works(truncated, expected):
    """The pre-existing truncation repair must not regress.

    Note the real bound: recovery handles truncation at a key boundary or a
    trailing comma, but NOT mid string *value* (``{"content": "def``), which
    still raises. That is pre-existing behaviour, asserted below so a future
    change to it is a deliberate decision rather than a silent drift.
    """
    assert parse_llm_json(truncated) == expected


def test_truncation_mid_string_value_still_raises():
    """Documents the known gap in truncation recovery."""
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json('[{"content": "abc"}, {"content": "def')


def test_genuinely_unparseable_still_raises():
    """Recovery must not paper over real garbage."""
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("this is not json at all")
