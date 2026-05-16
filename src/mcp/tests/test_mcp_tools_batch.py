# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pkb_batch orchestrator.

Coverage:
* DAG ordering (depends_on respected)
* Reference resolution (whole-string and substring)
* Cycle detection
* Cap enforcement (>10 ops, nesting)
* continue_on_error semantics
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.mcp_tools.batch import (
    _resolve_path,
    _substitute_refs,
    _topo_sort,
    pkb_batch,
)
from app.tool_registry import InvalidParamsError

# ----------------------------------------------------------- _resolve_path


def test_resolve_path_empty():
    assert _resolve_path({"a": 1}, "") == {"a": 1}


def test_resolve_path_dotted():
    val = {"artifact": {"id": "abc", "domain": "coding"}}
    assert _resolve_path(val, ".artifact.id") == "abc"
    assert _resolve_path(val, ".artifact.domain") == "coding"


def test_resolve_path_array_index():
    val = {"chunks": [{"id": "c0"}, {"id": "c1"}]}
    assert _resolve_path(val, ".chunks[0].id") == "c0"
    assert _resolve_path(val, ".chunks[1].id") == "c1"


def test_resolve_path_missing_returns_none():
    assert _resolve_path({"a": 1}, ".missing") is None
    assert _resolve_path({"chunks": [1]}, ".chunks[5]") is None


# ----------------------------------------------------------- _substitute_refs


def test_substitute_whole_string_returns_object():
    results = {"op_0": {"artifact": {"id": "abc"}, "list": [1, 2]}}
    # Whole-string match → return the value as-is (preserve types)
    assert _substitute_refs("${op_0.result.artifact}", results) == {"id": "abc"}
    assert _substitute_refs("${op_0.result.list}", results) == [1, 2]


def test_substitute_substring_interpolates_to_string():
    results = {"op_0": {"id": "abc"}}
    assert _substitute_refs("hello ${op_0.result.id} world", results) == "hello abc world"


def test_substitute_nested_dict_and_list():
    results = {"op_0": {"id": "a1"}}
    arg = {
        "ids": ["${op_0.result.id}", "static"],
        "options": {"primary_id": "${op_0.result.id}"},
    }
    out = _substitute_refs(arg, results)
    assert out == {"ids": ["a1", "static"], "options": {"primary_id": "a1"}}


def test_substitute_missing_op_raises():
    with pytest.raises(InvalidParamsError, match="has not"):
        _substitute_refs("${op_missing.result.x}", {})


# ----------------------------------------------------------- _topo_sort


def test_topo_sort_simple_chain():
    ops = [
        {"tool": "a", "arguments": {}, "op_id": "first"},
        {"tool": "b", "arguments": {}, "op_id": "second", "depends_on": ["first"]},
        {"tool": "c", "arguments": {}, "op_id": "third", "depends_on": ["second"]},
    ]
    order = _topo_sort(ops)
    assert order == [0, 1, 2]


def test_topo_sort_diamond():
    ops = [
        {"tool": "a", "arguments": {}, "op_id": "root"},
        {"tool": "b", "arguments": {}, "op_id": "left", "depends_on": ["root"]},
        {"tool": "b", "arguments": {}, "op_id": "right", "depends_on": ["root"]},
        {"tool": "c", "arguments": {}, "op_id": "join", "depends_on": ["left", "right"]},
    ]
    order = _topo_sort(ops)
    # root first, join last; left/right between in stable index order.
    assert order[0] == 0
    assert order[-1] == 3
    assert set(order[1:3]) == {1, 2}


def test_topo_sort_cycle_raises():
    ops = [
        {"tool": "a", "arguments": {}, "op_id": "a", "depends_on": ["b"]},
        {"tool": "b", "arguments": {}, "op_id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(InvalidParamsError, match="cycle"):
        _topo_sort(ops)


def test_topo_sort_missing_dep_raises():
    ops = [
        {"tool": "a", "arguments": {}, "op_id": "a", "depends_on": ["nonexistent"]},
    ]
    with pytest.raises(InvalidParamsError, match="missing op_id"):
        _topo_sort(ops)


def test_topo_sort_duplicate_id_raises():
    ops = [
        {"tool": "a", "arguments": {}, "op_id": "dup"},
        {"tool": "b", "arguments": {}, "op_id": "dup"},
    ]
    with pytest.raises(InvalidParamsError, match="duplicate"):
        _topo_sort(ops)


# ----------------------------------------------------------- pkb_batch end-to-end


@pytest.mark.asyncio
async def test_batch_runs_chain_and_passes_refs():
    """Two ops where the second consumes the first's output."""
    call_log = []

    async def fake_execute(name, args):
        call_log.append((name, args))
        if name == "first":
            return {"id": "abc"}
        if name == "second":
            return {"echo": args}
        raise AssertionError("unexpected")

    with patch("app.tools.execute_tool", side_effect=fake_execute):
        out = await pkb_batch(
            operations=[
                {"tool": "first", "arguments": {}, "op_id": "step1"},
                {
                    "tool": "second",
                    "arguments": {"upstream_id": "${step1.result.id}"},
                    "op_id": "step2",
                    "depends_on": ["step1"],
                },
            ],
        )

    assert out["status"] == "ok"
    assert out["completed"] == 2
    assert out["failed"] == 0
    # step2 received the resolved id
    assert call_log[1] == ("second", {"upstream_id": "abc"})


@pytest.mark.asyncio
async def test_batch_aborts_on_first_failure_by_default():
    call_log = []

    async def fake_execute(name, args):
        call_log.append(name)
        if name == "boom":
            raise RuntimeError("boom!")
        return {}

    with patch("app.tools.execute_tool", side_effect=fake_execute):
        out = await pkb_batch(
            operations=[
                {"tool": "ok", "arguments": {}, "op_id": "a"},
                {"tool": "boom", "arguments": {}, "op_id": "b"},
                {"tool": "after", "arguments": {}, "op_id": "c"},  # should NOT run
            ],
        )

    assert out["status"] == "aborted"
    assert out["completed"] == 1
    assert out["failed"] == 1
    assert "after" not in call_log


@pytest.mark.asyncio
async def test_batch_continue_on_error_runs_remaining():
    call_log = []

    async def fake_execute(name, args):
        call_log.append(name)
        if name == "boom":
            raise RuntimeError("boom!")
        return {}

    with patch("app.tools.execute_tool", side_effect=fake_execute):
        out = await pkb_batch(
            operations=[
                {"tool": "ok", "arguments": {}, "op_id": "a"},
                {"tool": "boom", "arguments": {}, "op_id": "b"},
                {"tool": "after", "arguments": {}, "op_id": "c"},
            ],
            continue_on_error=True,
        )

    assert out["status"] == "partial"
    assert out["completed"] == 2
    assert out["failed"] == 1
    assert "after" in call_log


@pytest.mark.asyncio
async def test_batch_refuses_more_than_10_ops():
    with pytest.raises(InvalidParamsError, match="max 10"):
        await pkb_batch(
            operations=[
                {"tool": "x", "arguments": {}}
                for _ in range(11)
            ],
        )


@pytest.mark.asyncio
async def test_batch_refuses_nested_batch():
    with pytest.raises(InvalidParamsError, match="cannot invoke itself"):
        await pkb_batch(
            operations=[
                {"tool": "pkb_batch", "arguments": {"operations": []}},
            ],
        )


@pytest.mark.asyncio
async def test_batch_refuses_empty_operations():
    with pytest.raises(InvalidParamsError, match="non-empty"):
        await pkb_batch(operations=[])
