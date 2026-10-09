# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Confirm tokens run once, for their caller, before they expire."""
from __future__ import annotations

import fakeredis
import pytest

from app.services.forget import confirm
from core.forget.registry import Subject

SUBJECTS = [Subject("artifact", "a" * 64), Subject("chunk", f"{'b' * 64}_0123456789abcdef")]


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


def test_a_token_returns_what_it_was_issued_for(redis):
    token = confirm.issue(redis, SUBJECTS, "trash", "mcp")
    got = confirm.consume(redis, token, "mcp")
    assert got.subjects == SUBJECTS and got.mode == "trash"


def test_a_token_works_once(redis):
    token = confirm.issue(redis, SUBJECTS, "permanent", "sdk:cerid-finance")
    confirm.consume(redis, token, "sdk:cerid-finance")
    with pytest.raises(confirm.ConfirmTokenError, match="already used"):
        confirm.consume(redis, token, "sdk:cerid-finance")


def test_another_caller_is_refused_and_the_token_is_spent(redis):
    token = confirm.issue(redis, SUBJECTS, "trash", "sdk:cerid-finance")
    with pytest.raises(confirm.ConfirmTokenError, match="another caller"):
        confirm.consume(redis, token, "sdk:cerid-trading")
    with pytest.raises(confirm.ConfirmTokenError):
        confirm.consume(redis, token, "sdk:cerid-finance")


def test_a_token_expires(redis):
    token = confirm.issue(redis, SUBJECTS, "trash", "mcp")
    assert 0 < redis.ttl(f"cerid:forget:confirm:{token}") <= confirm.TTL_SECONDS
    redis.delete(f"cerid:forget:confirm:{token}")
    with pytest.raises(confirm.ConfirmTokenError, match="expired"):
        confirm.consume(redis, token, "mcp")


@pytest.mark.parametrize("bad", ["", "x" * 129])
def test_a_malformed_token_is_refused(redis, bad):
    with pytest.raises(confirm.ConfirmTokenError, match="malformed"):
        confirm.consume(redis, bad, "mcp")


def test_tokens_are_unguessable_and_distinct(redis):
    tokens = {confirm.issue(redis, SUBJECTS, "trash", "mcp") for _ in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 32 for t in tokens)
