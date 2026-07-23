# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 post-audit M1-2 — R2 BYOK must not intercept OpenRouter ``:online`` ids.

RESEARCH (``…grok-4.3:online``) and EXPERT (``…grok-4.20:online``) tier ids use
OpenRouter's web-search suffix. With xai BYOK enabled, pre-fix ``byok_target``
stripped only ``openrouter/`` + vendor prefix and dispatched the remainder
(including ``:online``) to xAI, which rejects it. Unit harness for prepush.
"""
from __future__ import annotations

import pytest

_K_XAI = "xai-live"  # pragma: allowlist secret


def _enable_xai(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BYOK_DIRECT_PROVIDERS", "XAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "xai")
    monkeypatch.setenv("XAI_API_KEY", _K_XAI)


@pytest.mark.parametrize(
    "model_id",
    [
        "openrouter/x-ai/grok-4.3:online",
        "x-ai/grok-4.3:online",
        "openrouter/x-ai/grok-4.20:online",
        "x-ai/grok-4.20:online",
    ],
)
def test_byok_target_none_for_online_suffix(
    monkeypatch: pytest.MonkeyPatch, model_id: str
) -> None:
    from core.routing import provider_state

    _enable_xai(monkeypatch)
    assert provider_state.byok_target(model_id) is None, (
        f"{model_id!r} must fall through to OpenRouter (E1 R2)"
    )


def test_byok_target_still_resolves_non_online_xai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-online xAI ids remain direct-dispatchable when BYOK is enabled."""
    from core.routing import provider_state

    _enable_xai(monkeypatch)
    target = provider_state.byok_target("openrouter/x-ai/grok-4.20")
    assert target is not None
    assert target.provider == "xai"
    assert target.model == "grok-4.20"
