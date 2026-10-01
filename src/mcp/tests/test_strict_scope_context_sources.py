# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A query held strictly to named domains gets rows from those domains only.

Live stack, 2026-09-27: POST /agent/query with domains ["tasks"] and
query_scope "domain" reported domains_searched ["tasks"] and returned three
rows from ``conversations`` (the memory surface) and two from ``external``.
"""
from __future__ import annotations

import pytest

from app.routers.agents import _scoped_context_sources


def scope(sources, domains, *, strict=True, requested=True):
    return _scoped_context_sources(
        sources, domains, strict_domains=strict, requested_strict=requested,
    )


def test_a_strict_scope_the_request_asked_for_turns_the_web_and_memory_off() -> None:
    assert scope(None, ["tasks"]) == {"memory": False, "external": False}


def test_a_consumer_strict_by_registration_loses_memory_and_keeps_the_web() -> None:
    # The preservation gate runs as an unregistered client, strict by default,
    # against a near-empty corpus: its answer comes from the web.
    assert scope(None, ["general"], requested=False) == {"memory": False}
    assert scope({"external": True}, ["general"], requested=False) == {"external": True, "memory": False}


def test_it_keeps_what_the_caller_had_already_turned_off() -> None:
    out = scope({"kb": False, "memory": True, "external": True}, ["tasks"])
    assert out == {"kb": False, "memory": False, "external": False}


def test_memory_stays_when_conversations_is_a_named_domain() -> None:
    assert scope({"memory": True}, ["tasks", "conversations"]) == {"memory": True, "external": False}


def test_it_does_not_turn_a_source_on() -> None:
    assert scope({"memory": False}, ["conversations"]) == {"memory": False, "external": False}
    assert scope({"external": False}, ["general"], requested=False) == {"external": False, "memory": False}


@pytest.mark.parametrize(
    ("domains", "strict"),
    [(["tasks"], False), (None, True), ([], True), (None, False)],
)
def test_a_query_that_is_not_strictly_scoped_is_unchanged(domains, strict) -> None:
    given = {"memory": True, "external": True}
    assert scope(given, domains, strict=strict, requested=strict) is given
    assert scope(None, domains, strict=strict, requested=strict) is None


def test_it_does_not_change_the_dict_it_was_given() -> None:
    given = {"memory": True}
    scope(given, ["tasks"])
    assert given == {"memory": True}


def test_the_query_route_applies_it_before_it_reads_or_passes_the_sources() -> None:
    import inspect

    from app.routers import agents

    src = inspect.getsource(agents._agent_query_inner)
    applied = src.index("_scoped_context_sources(")
    assert applied < src.index("_cs = req.context_sources")
    assert applied < src.index("orchestrated_query(")
    assert applied < src.index("agent_query_full(")
    assert "requested_strict=bool(req.strict_domains)" in src
