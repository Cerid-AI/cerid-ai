# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Memory rows honor the same allow-list as the knowledge-base scan."""
from __future__ import annotations

from core.agents.query_agent import scope_memory_rows


def _row(domain: str) -> dict:
    return {"domain": domain, "content": "remembered", "source_type": "memory"}


def test_a_strict_scope_drops_memory_rows_outside_it() -> None:
    rows = [_row("conversations"), _row("anneal_lessons")]
    kept = scope_memory_rows(rows, strict=True, domains=["anneal_lessons"])
    assert [row["domain"] for row in kept] == ["anneal_lessons"]


def test_a_strict_scope_keeps_conversations_when_that_domain_was_named() -> None:
    rows = [_row("conversations")]
    kept = scope_memory_rows(rows, strict=True, domains=["conversations", "notes"])
    assert kept == rows


def test_a_non_strict_caller_keeps_every_memory_row() -> None:
    rows = [_row("conversations")]
    kept = scope_memory_rows(rows, strict=False, domains=["anneal_lessons"])
    assert kept == rows


def test_a_strict_caller_with_no_domains_keeps_nothing() -> None:
    assert scope_memory_rows([_row("conversations")], strict=True, domains=None) == []
