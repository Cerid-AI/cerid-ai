# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Alerting API — threshold-based metric alerts with webhook notifications.

Provides CRUD for alert rules and event history:

- ``POST /observability/alerts/`` — create alert rule
- ``GET  /observability/alerts/`` — list all rules
- ``GET  /observability/alerts/{rule_id}`` — get specific rule
- ``PUT  /observability/alerts/{rule_id}`` — update rule
- ``DELETE /observability/alerts/{rule_id}`` — delete rule
- ``GET  /observability/alerts/events`` — recent alert events
- ``POST /observability/alerts/evaluate`` — manually trigger evaluation
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ai-companion.alerts")

router = APIRouter(prefix="/observability/alerts", tags=["alerts"])

# Redis key constants
_RULES_KEY = "cerid:alerts:rules"
_EVENTS_KEY = "cerid:alerts:events"
_EVENTS_MAX = 1000

_OPERATORS = {"gt", "lt", "gte", "lte", "eq"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AlertRule(BaseModel):
    id: str = ""
    metric_name: str
    operator: str  # "gt", "lt", "gte", "lte", "eq"
    threshold: float
    window_minutes: int = 60
    webhook_url: str = ""
    enabled: bool = True
    description: str = ""


class AlertEvent(BaseModel):
    rule_id: str
    metric_name: str
    current_value: float
    threshold: float
    operator: str
    triggered_at: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis():
    from deps import get_redis

    return get_redis()


def _iso_now() -> str:
    from utils.time import utcnow_iso

    return utcnow_iso()


def _compare(value: float, operator: str, threshold: float) -> bool:
    """Evaluate a threshold comparison."""
    if operator == "gt":
        return value > threshold
    if operator == "lt":
        return value < threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    return False


async def _notify_webhook(url: str, event: AlertEvent) -> None:
    """POST alert event to webhook URL with 10s timeout."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=event.model_dump())
    except Exception as exc:
        logger.warning("Webhook notification failed for %s: %s", url, exc)


async def evaluate_alerts(redis_client) -> list[AlertEvent]:
    """Check all enabled rules against current metric values."""
    from utils.metrics import get_metrics_collector

    collector = get_metrics_collector()
    events: list[AlertEvent] = []

    raw_rules = redis_client.hgetall(_RULES_KEY)
    if not raw_rules:
        return events

    for _rule_id, rule_json in raw_rules.items():
        try:
            rule_data = json.loads(rule_json)
            rule = AlertRule(**rule_data)
        except (json.JSONDecodeError, Exception):
            continue

        if not rule.enabled:
            continue

        # Get current aggregated metric value
        aggregated = collector.get_aggregated_metrics(rule.window_minutes)
        metric_data = aggregated.get(rule.metric_name, {})
        current_value = metric_data.get("avg")

        if current_value is None:
            continue

        if _compare(current_value, rule.operator, rule.threshold):
            event = AlertEvent(
                rule_id=rule.id,
                metric_name=rule.metric_name,
                current_value=current_value,
                threshold=rule.threshold,
                operator=rule.operator,
                triggered_at=_iso_now(),
                message=(
                    f"{rule.metric_name} {rule.operator} {rule.threshold}: "
                    f"current value {current_value}"
                ),
            )
            events.append(event)

            # Store event in Redis list
            redis_client.lpush(_EVENTS_KEY, json.dumps(event.model_dump()))
            redis_client.ltrim(_EVENTS_KEY, 0, _EVENTS_MAX - 1)

            # Optionally notify webhook
            if rule.webhook_url:
                await _notify_webhook(rule.webhook_url, event)

    return events


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/", response_model=AlertRule)
async def create_alert_rule(rule: AlertRule):
    """Create a new alert rule."""
    if rule.operator not in _OPERATORS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid operator: {rule.operator}. Valid: {', '.join(sorted(_OPERATORS))}",
        )
    if not rule.id:
        rule.id = str(uuid.uuid4())

    redis = _get_redis()
    redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))
    return rule


@router.get("/", response_model=list[AlertRule])
def list_alert_rules():
    """List all alert rules."""
    redis = _get_redis()
    raw = redis.hgetall(_RULES_KEY)
    rules: list[AlertRule] = []
    for _id, rule_json in (raw or {}).items():
        try:
            rules.append(AlertRule(**json.loads(rule_json)))
        except (json.JSONDecodeError, Exception):
            continue
    return rules


@router.get("/events", response_model=list[AlertEvent])
def list_alert_events():
    """List recent alert events (last 100)."""
    redis = _get_redis()
    raw = redis.lrange(_EVENTS_KEY, 0, 99)
    events: list[AlertEvent] = []
    for entry in raw or []:
        try:
            events.append(AlertEvent(**json.loads(entry)))
        except (json.JSONDecodeError, Exception):
            continue
    return events


@router.get("/{rule_id}", response_model=AlertRule)
def get_alert_rule(rule_id: str):
    """Get a specific alert rule."""
    redis = _get_redis()
    raw = redis.hget(_RULES_KEY, rule_id)
    if not raw:
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found")
    return AlertRule(**json.loads(raw))


@router.put("/{rule_id}", response_model=AlertRule)
async def update_alert_rule(rule_id: str, rule: AlertRule):
    """Update an existing alert rule."""
    redis = _get_redis()
    if not redis.hexists(_RULES_KEY, rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found")
    if rule.operator not in _OPERATORS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid operator: {rule.operator}. Valid: {', '.join(sorted(_OPERATORS))}",
        )
    rule.id = rule_id
    redis.hset(_RULES_KEY, rule_id, json.dumps(rule.model_dump()))
    return rule


@router.delete("/{rule_id}")
def delete_alert_rule(rule_id: str):
    """Delete an alert rule."""
    redis = _get_redis()
    deleted = redis.hdel(_RULES_KEY, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found")
    return {"detail": f"Alert rule {rule_id} deleted"}


@router.post("/evaluate", response_model=list[AlertEvent])
async def trigger_evaluation():
    """Manually trigger alert evaluation against current metrics."""
    redis = _get_redis()
    return await evaluate_alerts(redis)
