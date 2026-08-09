# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Custom Smart RAG — per-source weight storage (Phase I).

Lets a Pro user tune the retrieval blending weights for each
configured data source. Weights are multiplicative against the
default per-source confidence:

    final_score = base_score × user_weight

Stored as a Redis hash:

    cerid:rag:weights:global             (single-user mode)
    cerid:rag:weights:user:{user_id}     (multi-user mode)

Each entry maps `source_name → str(float)` in the range [0.0, 2.0]
with 1.0 as the no-change default.

Source naming:
    - For DataSource subclasses: their `name` attribute (e.g. "gmail",
      "google_calendar", "apple_calendar", "wikipedia").
    - For KB domain weights: prefixed `kb:<domain>` (e.g. "kb:notes",
      "kb:mail", "kb:meetings", "kb:personal").

The retrieval blending step looks up both keys when scoring results.
Missing keys = default 1.0 (no change). Out-of-range weights are
clamped silently at read time.
"""
from __future__ import annotations

import logging
from typing import Any

from core.context.identity import get_user_id

logger = logging.getLogger("ai-companion.rag_weights")

# ── tunables ──────────────────────────────────────────────────────────

MIN_WEIGHT = 0.0
MAX_WEIGHT = 2.0
DEFAULT_WEIGHT = 1.0

GLOBAL_KEY = "cerid:rag:weights:global"
USER_KEY_PREFIX = "cerid:rag:weights:user:"


# ── key helpers ───────────────────────────────────────────────────────

def _is_multi_user() -> bool:
    try:
        from config.features import CERID_MULTI_USER
        return bool(CERID_MULTI_USER)
    except ImportError:
        return False


def _redis_key(user_id: str | None = None) -> str:
    """Resolve the Redis hash key for the active user.

    - Multi-user: returns the per-user key, falling back to global when
      no user is bound (the request didn't carry tenant context).
    - Single-user: always returns the global key.
    """
    if not _is_multi_user():
        return GLOBAL_KEY
    uid = user_id if user_id is not None else get_user_id()
    if not uid:
        return GLOBAL_KEY
    return f"{USER_KEY_PREFIX}{uid}"


def _clamp(weight: float) -> float:
    return max(MIN_WEIGHT, min(MAX_WEIGHT, float(weight)))


# ── readers ───────────────────────────────────────────────────────────

def get_weights(user_id: str | None = None) -> dict[str, float]:
    """Return the active weight map. Empty dict on Redis unavailable
    (callers treat empty as "all defaults = 1.0").
    """
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is None:
            return {}
        raw = redis.hgetall(_redis_key(user_id))
        out: dict[str, float] = {}
        for k, v in (raw or {}).items():
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else str(v)
            try:
                out[key] = _clamp(float(val))
            except (ValueError, TypeError):
                continue
        return out
    except Exception as exc:  # noqa: BLE001 — Redis/DI can fail many ways
        logger.warning("get_weights failed: %s", exc)
        return {}


def get_weight(source: str, user_id: str | None = None) -> float:
    """Single-source weight lookup, defaulting to 1.0 on miss/error."""
    weights = get_weights(user_id)
    return weights.get(source, DEFAULT_WEIGHT)


def is_active() -> bool:
    """Cheap check: does the active user have any non-default weights?

    Hot-path retrieval should call this once and short-circuit when
    no weights are set (the common case for free-tier users).
    """
    try:
        from config.features import is_feature_enabled
        if not is_feature_enabled("custom_smart_rag"):
            return False
    except ImportError:
        return False
    weights = get_weights()
    if not weights:
        return False
    return any(abs(w - DEFAULT_WEIGHT) > 1e-9 for w in weights.values())


# ── writers ───────────────────────────────────────────────────────────

def set_weights(updates: dict[str, float], user_id: str | None = None) -> dict[str, float]:
    """Bulk-replace weights for the listed sources. Returns the post-update
    map for confirmation."""
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is None:
            raise RuntimeError("Redis unavailable — weights not persisted")
        key = _redis_key(user_id)
        for source, weight in updates.items():
            redis.hset(key, source, str(_clamp(weight)))
        return get_weights(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_weights failed: %s", exc)
        raise


def reset_weights(user_id: str | None = None) -> None:
    """Delete every weight for the active user (revert to defaults)."""
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is None:
            return
        redis.delete(_redis_key(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset_weights failed: %s", exc)


# ── apply (hot-path helper) ───────────────────────────────────────────

def apply_to_result(
    score: float,
    *,
    source_name: str | None = None,
    domain: str | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """Scale `score` by the user's configured multipliers.

    Two lookup keys may apply to one result:
      - source_name (e.g. "gmail", "wikipedia") — the DataSource name
        when the result came from `registry.query_all()`
      - domain (e.g. "notes", "mail") — the KB collection, looked up
        under the `kb:<domain>` prefix

    Both apply multiplicatively when both are set (e.g. a Gmail
    DataSource result also living under `kb:mail` would receive
    both weights). Most results only have one of the two.

    Callers in the retrieval blending step should pass a single
    `weights` map fetched outside the loop (avoids one Redis call per
    candidate; see Day 2 wiring in query_agent.py).
    """
    if weights is None:
        weights = get_weights()
    if not weights:
        return score
    multiplier = 1.0
    if source_name and source_name in weights:
        multiplier *= weights[source_name]
    if domain:
        kb_key = f"kb:{domain}"
        if kb_key in weights:
            multiplier *= weights[kb_key]
    return score * multiplier


# ── known-sources enumeration (for the UI's source picker) ────────────

def known_sources() -> list[dict[str, Any]]:
    """List the sources the user can assign weights to.

    Pulls from:
      - DataSourceRegistry (Gmail, Google Calendar, Wikipedia, etc.)
      - config.taxonomy.DOMAINS for KB collections (each prefixed kb:)

    Returns: list of {name, kind: "data_source" | "kb_domain",
    description, default_enabled, current_weight}.
    """
    weights = get_weights()
    out: list[dict[str, Any]] = []

    # DataSources
    try:
        from app.data_sources import registry
        for ds in registry.list_sources():
            out.append({
                "name": ds["name"],
                "kind": "data_source",
                "description": ds.get("description", ""),
                "default_enabled": ds.get("enabled", False),
                "current_weight": weights.get(ds["name"], DEFAULT_WEIGHT),
            })
    except ImportError:
        pass

    # KB domains
    try:
        from config.taxonomy import TAXONOMY
        for domain, meta in TAXONOMY.items():
            name = f"kb:{domain}"
            out.append({
                "name": name,
                "kind": "kb_domain",
                "description": meta.get("description", f"KB domain: {domain}"),
                "default_enabled": True,
                "current_weight": weights.get(name, DEFAULT_WEIGHT),
            })
    except ImportError:
        pass

    return out
