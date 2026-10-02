# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Replay the captured completions through the parsers.

corpus/<model>.json holds what each model wrote for the same requests
(capture_corpus.py). A model or quant refresh that changes the call format makes
these fail instead of dropping arguments in production.

The models are every row of the shipped catalogs/, plus models.json when a host
keeps its own catalog there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import serve
from conftest import HAVE_HARMONY, HAVE_PARSERS, need

STACK = Path(serve.__file__).parent
CORPUS = Path(__file__).resolve().parent / "corpus"
CATALOG_FILES = sorted((STACK / "catalogs").glob("*.json")) + [
    p for p in (STACK / "models.json",) if p.exists()
]
ROWS = [
    (catalog.name, name, spec)
    for catalog in CATALOG_FILES
    for name, spec in json.loads(catalog.read_text())["models"].items()
]
MODELS = {name: spec for _, name, spec in ROWS}
FILES = sorted(CORPUS.glob("*.json"))
CASES = [(f.stem, case) for f in FILES for case in json.loads(f.read_text())["cases"]]


def test_a_model_name_has_one_tool_format_in_every_catalog():
    for catalog, name, spec in ROWS:
        assert spec.get("tool_format") == MODELS[name].get("tool_format"), f"{name} in {catalog}"


def test_every_chat_model_with_a_tool_format_has_a_corpus():
    with_tools = {name for name, spec in MODELS.items() if spec.get("tool_format")}
    assert with_tools == {f.stem for f in FILES}


def test_every_corpus_covers_the_required_cases():
    for f in FILES:
        corpus = json.loads(f.read_text())
        names = {c["name"] for c in corpus["cases"]}
        assert {"single_call", "plain_text_with_tools", "tool_result_followup"} <= names, f.stem
        if corpus["tool_format"] != "harmony":
            assert "parallel_calls" in names, f.stem
        assert corpus["tool_format"] == MODELS[f.stem]["tool_format"]


@pytest.mark.parametrize(("model", "case"), CASES, ids=[f"{m}-{c['name']}" for m, c in CASES])
def test_replay(model, case):
    tool_format = MODELS[model]["tool_format"]
    if tool_format == "harmony":
        need(HAVE_HARMONY, "openai-harmony")
        _, enc = serve._harmony()
        tokens = enc.encode(case["completion"], allowed_special="all")
    else:
        need(HAVE_PARSERS, "mlx-lm")
        tokens = []
    tools, _ = serve.chat_tools(case["request"])
    parsed = serve.parse_reply(tool_format, case["completion"], tokens, tools, case["done_reason"])
    assert {k: parsed[k] for k in ("content", "calls", "finish")} == case["expect"]


def test_expectations_say_what_the_requests_asked_for():
    # `expect` was the parser's reading at capture time. Pin its meaning here,
    # so a regenerated corpus cannot quietly bless a wrong parse.
    for model, case in CASES:
        expect = case["expect"]
        calls = [(c["name"], c["arguments"]) for c in expect["calls"]]
        if case["name"] == "single_call":
            assert expect["finish"] == "tool_calls", model
            assert [n for n, _ in calls] == ["get_weather"] and calls[0][1]["location"] == "Paris", model
        elif case["name"] == "parallel_calls":
            assert sorted(a["location"] for _, a in calls) == ["Paris", "Tokyo"], model
        elif case["name"] == "typed_arguments":
            assert calls == [("search_files", {"pattern": "test_*.py", "max_results": 5, "include_hidden": True})], model
        elif case["name"] == "named_choice":
            assert [n for n, _ in calls] == ["get_weather"], model
        elif case["name"] == "plain_text_with_tools":
            assert expect["finish"] == "stop" and not calls and expect["content"].strip() == "42", model
        elif case["name"] == "tool_result_followup":
            assert expect["finish"] == "stop" and not calls and "18" in expect["content"], model

