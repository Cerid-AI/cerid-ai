# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for parse_tag_object — the canonical artifact-tags coercion.

Regression cover for the tags-as-dict bug class: list_artifacts returns
`tags` as a raw JSON string, and digest/inbox readers do `tags.get(...)`.
"""
from __future__ import annotations

from core.utils.artifact_tags import parse_tag_object


def test_json_string_object_is_parsed():
    raw = '{"category": "urgent", "subject": "x"}'
    assert parse_tag_object(raw) == {"category": "urgent", "subject": "x"}


def test_already_dict_passthrough():
    assert parse_tag_object({"category": "actionable"}) == {"category": "actionable"}


def test_json_list_string_yields_empty_dict():
    # The canonical "[]" storage shape — a list is "no object metadata".
    assert parse_tag_object("[]") == {}
    assert parse_tag_object('["a", "b"]') == {}


def test_none_and_empty_yield_empty_dict():
    assert parse_tag_object(None) == {}
    assert parse_tag_object("") == {}


def test_malformed_json_yields_empty_dict_not_raise():
    assert parse_tag_object("{not json") == {}


def test_get_on_result_never_raises_on_string_input():
    # The exact call shape that used to AttributeError in daily_digest/digests.
    assert parse_tag_object("[]").get("category") is None
