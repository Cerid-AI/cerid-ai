# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observability metrics collector backed by Redis sorted sets.

Stores time-series metrics in Redis sorted sets keyed by timestamp.
Supports recording, retrieval, and aggregation (avg, p50, p95, p99)
with automatic expiration of old entries (7-day TTL).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from errors import CeridError

logger = logging.getLogger("ai-companion.metrics")

# Redis key prefix for all observability metrics
_KEY_PREFIX = "cerid:metrics"
# TTL for metric entries (7 days)
_METRIC_TTL_SECONDS = 7 * 24 * 60 * 60

# Known metric names (for validation and documentation)
METRIC_NAMES = frozenset({
    "query_latency_ms",
    "retrieval_latency_ms",
    "llm_latency_ms",
    "llm_cost_usd",
    "retrieval_ndcg",
    "cache_hit_rate",
    "verification_accuracy",
    "queries_per_minute",
})

# Model pricing table (USD per 1M tokens) --input/output
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "anthropic/claude-opus-4.6": (5.0, 25.0),
    "x-ai/grok-4.1-fast": (0.20, 0.50),
    "openai/o3-mini": (1.10, 4.40),
    "google/gemini-3-flash-preview": (0.50, 3.0),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.5-flash": (0.30, 2.50),
    "meta-llama/llama-3.3-70b-instruct": (0.10, 0.32),
}

# Ollama models — explicitly free (local inference, zero cloud cost).
# Listed separately so estimate_cost() returns 0.0 intentionally,
# distinguishable from "unknown model" which also returns 0.0.
_OLLAMA_MODEL_PREFIXES = ("phi", "llama", "gemma", "mistral", "codellama")


def is_ollama_model(model_id: str) -> bool:
    """Check if a model ID is a local Ollama model (free inference)."""
    clean = model_id.lower().split(":")[0]
    return any(clean.startswith(p) for p in _OLLAMA_MODEL_PREFIXES)


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate LLM cost in USD based on model and token counts.

    Strips common prefixes (``openrouter/``) before looking up pricing.
    Returns 0.0 for Ollama models (explicitly free) and unknown models.
    """
    # Ollama models are always free
    if is_ollama_model(model_id):
        return 0.0

    # Normalize model ID
    clean_id = model_id
    for prefix in ("openrouter/",):
        if clean_id.startswith(prefix):
            clean_id = clean_id[len(prefix):]

    pricing = _MODEL_PRICING.get(clean_id)
    if not pricing:
        return 0.0

    input_cost_per_token = pricing[0] / 1_000_000
    output_cost_per_token = pricing[1] / 1_000_000
    return input_tokens * input_cost_per_token + output_tokens * output_cost_per_token


@dataclass
class MetricPoint:
    """A single metric data point."""

    timestamp: float
    value: float
    tags: dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Redis-backed time-series metrics collector.

    Stores metrics as Redis sorted sets with scores = timestamps.
    Members are JSON-encoded ``{value, tags}`` payloads.

    Args:
        redis_client: A connected Redis client instance.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    def _key(self, name: str) -> str:
        return f"{_KEY_PREFIX}:{name}"

    def record_metric(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Append a metric data point to the time series.

        Args:
            name: Metric name (e.g. ``query_latency_ms``).
            value: Numeric value to record.
            tags: Optional key-value tags (e.g. ``{"model": "gpt-4o-mini"}``).
        """
        ts = time.time()
        member = json.dumps({"v": value, "t": tags or {}, "ts": ts})
        key = self._key(name)
        try:
            self._redis.zadd(key, {member: ts})
            self._redis.expire(key, _METRIC_TTL_SECONDS)
        except (CeridError, ValueError, OSError, RuntimeError) as exc:
            logger.warning("Failed to record metric %s: %s", name, exc)

    def get_metrics(
        self,
        name: str,
        window_minutes: int = 60,
    ) -> list[MetricPoint]:
        """Retrieve recent metric data points within the given time window.

        Args:
            name: Metric name.
            window_minutes: How many minutes back to look.

        Returns:
            List of :class:`MetricPoint` sorted by timestamp ascending.
        """
        now = time.time()
        min_ts = now - window_minutes * 60
        key = self._key(name)
        try:
            raw_entries = self._redis.zrangebyscore(key, min_ts, now)
        except (CeridError, ValueError, OSError, RuntimeError) as exc:
            logger.warning("Failed to get metrics %s: %s", name, exc)
            return []

        points: list[MetricPoint] = []
        for raw in raw_entries:
            try:
                entry = json.loads(raw)
                points.append(MetricPoint(
                    timestamp=entry["ts"],
                    value=entry["v"],
                    tags=entry.get("t", {}),
                ))
            except (json.JSONDecodeError, KeyError):
                continue
        return points

    def get_aggregated_metrics(
        self,
        window_minutes: int = 60,
    ) -> dict[str, dict[str, float | None]]:
        """Return aggregated statistics for all known metrics.

        Returns a dict keyed by metric name with ``avg``, ``p50``, ``p95``,
        ``p99``, ``min``, ``max``, and ``count`` for each.
        """
        result: dict[str, dict[str, float | None]] = {}
        for name in METRIC_NAMES:
            points = self.get_metrics(name, window_minutes)
            result[name] = self._aggregate(points)
        return result

    def get_cost_breakdown(
        self,
        window_minutes: int = 60,
    ) -> dict[str, float]:
        """Return LLM cost breakdown by model tag over the given window.

        Returns a dict of ``{model_id: total_cost_usd}``.
        """
        points = self.get_metrics("llm_cost_usd", window_minutes)
        by_model: dict[str, float] = {}
        for pt in points:
            model = pt.tags.get("model", "unknown")
            by_model[model] = by_model.get(model, 0.0) + pt.value
        return by_model

    def cleanup_expired(self, name: str) -> int:
        """Remove entries older than the TTL from a metric's sorted set.

        Returns the number of entries removed.
        """
        cutoff = time.time() - _METRIC_TTL_SECONDS
        key = self._key(name)
        try:
            return self._redis.zremrangebyscore(key, 0, cutoff) or 0
        except (CeridError, ValueError, OSError, RuntimeError) as exc:
            logger.warning("Failed to cleanup metric %s: %s", name, exc)
            return 0

    @staticmethod
    def _aggregate(points: list[MetricPoint]) -> dict[str, float | None]:
        """Compute summary statistics from a list of metric points."""
        if not points:
            return {
                "avg": None, "p50": None, "p95": None, "p99": None,
                "min": None, "max": None, "count": 0,
            }

        values = sorted(pt.value for pt in points)
        n = len(values)
        return {
            "avg": sum(values) / n,
            "p50": values[n // 2],
            "p95": values[int(n * 0.95)] if n >= 2 else values[-1],
            "p99": values[int(n * 0.99)] if n >= 2 else values[-1],
            "min": values[0],
            "max": values[-1],
            "count": n,
        }


# ---------------------------------------------------------------------------
# Module-level singleton for convenient access
# ---------------------------------------------------------------------------

_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Return the global MetricsCollector (lazy-initialized from deps)."""
    global _collector
    if _collector is None:
        from deps import get_redis
        _collector = MetricsCollector(get_redis())
    return _collector


def reset_metrics_collector() -> None:
    """Reset the singleton (for testing)."""
    global _collector
    _collector = None
