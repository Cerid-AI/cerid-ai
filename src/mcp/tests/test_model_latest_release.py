# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Every OpenRouter call goes to the newest release of its model's family, and
a model that reasons by default is asked for its lowest effort on internal calls."""
from __future__ import annotations

import json

import pytest

import config
from core.routing import model_catalog as mc

CREATED = {
    "x-ai/grok-4.3": 100.0,
    "x-ai/grok-4.20": 90.0,     # a higher number, an older release
    "x-ai/grok-4.7": 300.0,
    "openai/gpt-4o-mini": 10.0,
    "openai/gpt-6-luna": 200.0,
    "openai/gpt-6.1-luna": 400.0,
    "openai/gpt-6-luna-pro": 200.0,
    "meta-llama/llama-4-scout": 50.0,
    "meta-llama/llama-4-maverick": 50.0,
}
IDS = list(CREATED)


def test_the_newest_release_wins_not_the_highest_number():
    assert mc.resolve_latest("openrouter/x-ai/grok-4.3", IDS, CREATED) == "openrouter/x-ai/grok-4.7"
    assert mc.resolve_latest("x-ai/grok-4.20", IDS, CREATED) == "x-ai/grok-4.7"


def test_the_web_search_overlay_is_kept():
    assert mc.resolve_latest("openrouter/x-ai/grok-4.3:online", IDS, CREATED) == "openrouter/x-ai/grok-4.7:online"


def test_a_renamed_line_moves_to_its_successor_then_to_its_newest_release():
    assert mc.resolve_latest("openai/gpt-4o-mini", IDS, CREATED) == "openai/gpt-6.1-luna"


def test_a_successor_missing_from_the_catalog_moves_nothing():
    assert mc.resolve_latest("openai/gpt-4o-mini", ["openai/gpt-4o-mini"], {"openai/gpt-4o-mini": 1.0}) == "openai/gpt-4o-mini"


@pytest.mark.parametrize("model", ["openai/gpt-6-luna-pro", "meta-llama/llama-4-scout"])
def test_a_variant_or_size_stays_itself(model):
    assert mc.resolve_latest(model, IDS, CREATED) == model


def _snapshot(tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(config, "MODEL_CATALOG_SNAPSHOT_PATH", str(tmp_path / "catalog.json"))
    monkeypatch.setattr(config, "MODEL_AUTO_UPDATE_ENABLED", True)
    mc.save_catalog_snapshot(catalog)


def test_dispatch_resolves_through_the_snapshot_and_only_when_enabled(tmp_path, monkeypatch):
    assert mc.current_model_id("openrouter/x-ai/grok-4.3") == "openrouter/x-ai/grok-4.3"  # no snapshot yet
    _snapshot(tmp_path, monkeypatch, [{"id": i, "created": c} for i, c in CREATED.items()])
    assert mc.current_model_id("openrouter/x-ai/grok-4.3") == "openrouter/x-ai/grok-4.7"
    monkeypatch.setattr(config, "MODEL_AUTO_UPDATE_ENABLED", False)
    assert mc.current_model_id("openrouter/x-ai/grok-4.3") == "openrouter/x-ai/grok-4.3"


def test_an_offline_refresh_keeps_the_last_snapshot(tmp_path, monkeypatch):
    _snapshot(tmp_path, monkeypatch, [{"id": "x-ai/grok-4.7", "created": 1}])
    mc.save_catalog_snapshot([])
    assert "x-ai/grok-4.7" in json.loads((tmp_path / "catalog.json").read_text())["created"]


REASONING = [
    {"id": "openai/gpt-6-luna", "created": 1, "reasoning": {"default_enabled": True, "supported_efforts": ["high", "low", "none"]}},
    {"id": "x-ai/grok-4.7", "created": 1, "reasoning": {"mandatory": True, "supported_efforts": ["high", "medium", "low"]}},
    {"id": "anthropic/claude-haiku-5.5", "created": 1, "reasoning": {"mandatory": False, "supported_efforts": ["high", "low"]}},
    {"id": "openai/gpt-4o-mini", "created": 1, "reasoning": None},
]


def test_only_a_model_that_reasons_by_default_gets_a_floor():
    assert mc.catalog_reasoning_floor(REASONING) == {"openai/gpt-6-luna": "none", "x-ai/grok-4.7": "low"}


def test_internal_calls_ask_for_the_floor_and_make_room_for_unavoidable_reasoning(tmp_path, monkeypatch):
    from core.utils.llm_client import _REASONING_HEADROOM, _apply_reasoning_floor

    _snapshot(tmp_path, monkeypatch, REASONING)
    luna = {"model": "openai/gpt-6-luna", "max_tokens": 100}
    _apply_reasoning_floor(luna)
    assert luna == {"model": "openai/gpt-6-luna", "max_tokens": 100, "reasoning": {"effort": "none"}}

    grok = {"model": "x-ai/grok-4.7:online", "max_tokens": 100}
    _apply_reasoning_floor(grok)
    assert grok["reasoning"] == {"effort": "low"} and grok["max_tokens"] == 100 + _REASONING_HEADROOM

    haiku = {"model": "anthropic/claude-haiku-5.5", "max_tokens": 100}
    _apply_reasoning_floor(haiku)
    assert "reasoning" not in haiku  # an effort would turn its reasoning on

    chosen = {"model": "x-ai/grok-4.7", "max_tokens": 100, "reasoning": {"effort": "high"}}
    _apply_reasoning_floor(chosen)
    assert chosen == {"model": "x-ai/grok-4.7", "max_tokens": 100, "reasoning": {"effort": "high"}}


def test_made_up_ids_cannot_grow_the_dispatch_memo_without_limit(tmp_path, monkeypatch):
    _snapshot(tmp_path, monkeypatch, [{"id": i, "created": c} for i, c in CREATED.items()])
    for n in range(mc._MAX_RESOLVED * 3):
        mc.current_model_id(f"made-up/model-{n}")
    assert len(mc._resolved) <= mc._MAX_RESOLVED
    long_id = "x/" + "a" * (mc._MAX_MODEL_ID_LEN + 1)
    assert mc.current_model_id(long_id) == long_id and long_id not in mc._resolved
    assert mc.current_model_id("openrouter/x-ai/grok-4.3") == "openrouter/x-ai/grok-4.7"
