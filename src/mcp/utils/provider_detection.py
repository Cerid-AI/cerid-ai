# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical provider detection — the ONE way to check provider status.

All endpoints that need to know which LLM providers are configured MUST
use ``detect_all_providers()`` from this module.  No other code path
should read provider env vars directly.
"""
from __future__ import annotations

import os

_PROVIDER_ENV_VARS: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


def _clean_key(env_var: str) -> str:
    """Strip quotes and whitespace from an env-var value."""
    return os.getenv(env_var, "").strip().strip('"').strip("'")


def detect_all_providers() -> dict[str, dict]:
    """Return status for every known cloud provider.

    Always includes all 4 providers (even unconfigured ones) so the
    frontend can iterate a predictable set.

    Returns::

        {
            "openrouter": {
                "configured": True,
                "key_env_var": "OPENROUTER_API_KEY",
                "key_present": True,
                "key_preview": "sk-o...7f3a",
            },
            ...
        }
    """
    result: dict[str, dict] = {}
    for provider_id, env_var in _PROVIDER_ENV_VARS.items():
        key = _clean_key(env_var)
        result[provider_id] = {
            "configured": bool(key),
            "key_env_var": env_var,
            "key_present": bool(key),
            "key_preview": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("***" if key else ""),
        }
    return result


def get_env_key(provider_id: str) -> str:
    """Return the cleaned API key value for *provider_id*, or ``""``."""
    env_var = _PROVIDER_ENV_VARS.get(provider_id, "")
    return _clean_key(env_var) if env_var else ""
