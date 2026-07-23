# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 CR-097: model_providers config plane — env takes precedence for API keys,
and plaintext keys are never persisted to Redis.

Pre-fix, load_config returned the Redis snapshot verbatim (Redis-first), so the
first PUT /providers/config froze an env snapshot — including plaintext keys —
into Redis, after which env key rotations were invisible. Synthetic (fake Redis
+ monkeypatched env), no stack."""
from __future__ import annotations

import json

import pytest

from core.routing.model_providers import (
    _REDIS_KEY,
    ModelProviderConfig,
    ProviderState,
    load_config,
    save_config,
)

# Fake fixture keys hoisted to module constants so detect-secrets (which scans
# tracked files) allowlists each unique value once, instead of flagging inline
# literals in the test bodies.
_FIXTURE_SECRET = "sk-fixture-secret"  # pragma: allowlist secret
_FIXTURE_OLD = "sk-fixture-old"  # pragma: allowlist secret
_FIXTURE_ROTATED = "sk-fixture-rotated"  # pragma: allowlist secret


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        self.store[key] = value


def test_save_config_strips_plaintext_keys() -> None:
    """A persisted config must not carry plaintext API keys into Redis."""
    r = _FakeRedis()
    save_config(r, ModelProviderConfig(providers={"openai": ProviderState(enabled=True, api_key=_FIXTURE_SECRET)}))
    doc = json.loads(r.store[_REDIS_KEY])
    assert doc["providers"]["openai"]["api_key"] == "", "plaintext key leaked into Redis"


def test_load_config_env_precedence_over_redis_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key rotated in env wins over the (stripped) Redis snapshot — the
    documented env-precedence, previously inverted (CR-097)."""
    r = _FakeRedis()
    # A prior PUT persisted a snapshot with openai enabled.
    save_config(r, ModelProviderConfig(providers={"openai": ProviderState(enabled=True, api_key=_FIXTURE_OLD)}))
    # Operator rotates the key via .env / setup.
    monkeypatch.setenv("OPENAI_API_KEY", _FIXTURE_ROTATED)

    cfg = load_config(r)

    assert cfg.providers["openai"].api_key == _FIXTURE_ROTATED, "env key rotation must be visible"
    assert cfg.providers["openai"].enabled is True, "structural state persists from Redis"
