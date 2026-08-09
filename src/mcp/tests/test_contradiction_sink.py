# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the contradiction-ledger DI sink (core→app decoupling)."""

from __future__ import annotations

import core.agents.hallucination.contradiction_sink as cs


def teardown_function(_fn: object) -> None:
    cs.set_contradiction_sink(None)  # type: ignore[arg-type]


def test_unwired_returns_none() -> None:
    cs.set_contradiction_sink(None)  # type: ignore[arg-type]
    assert cs.get_contradiction_sink() is None


def test_set_get_roundtrip() -> None:
    async def _sink(**_kw: object) -> None:  # pragma: no cover - identity
        return None

    cs.set_contradiction_sink(_sink)
    assert cs.get_contradiction_sink() is _sink


def test_stable_id_is_deterministic_and_short() -> None:
    a = cs.stable_id("claim text", "artifact-1")
    b = cs.stable_id("claim text", "artifact-1")
    c = cs.stable_id("claim text", "artifact-2")
    assert a == b  # same inputs → same id (idempotent re-detection)
    assert a != c  # different artifact → different id
    assert len(a) == 16 and all(ch in "0123456789abcdef" for ch in a)


def test_stable_id_handles_none() -> None:
    # must not raise on None parts
    assert isinstance(cs.stable_id(None, "x"), str)
