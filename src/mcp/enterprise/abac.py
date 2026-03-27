# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Attribute-Based Access Control engine with Redis-backed policy storage."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("ai-companion.enterprise.abac")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ABACRule:
    """A single ABAC rule matching subject/resource attributes to an effect."""

    subject_attrs: dict = field(default_factory=dict)
    resource_attrs: dict = field(default_factory=dict)
    action: str = "*"
    effect: str = "allow"  # "allow" | "deny"


@dataclass
class ABACPolicy:
    """Ordered collection of ABAC rules with first-match evaluation."""

    rules: list[ABACRule] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, subject: dict, resource: dict, action: str) -> str:
        """Evaluate the policy against a request context.

        Returns ``"allow"`` or ``"deny"``.  Default-deny when no rule matches.
        """
        for rule in self.rules:
            if self._matches(rule, subject, resource, action):
                return rule.effect
        return "deny"

    # ------------------------------------------------------------------
    # Serialization helpers (Redis persistence)
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps([asdict(r) for r in self.rules])

    @classmethod
    def from_json(cls, raw: str) -> ABACPolicy:
        data = json.loads(raw)
        rules = [ABACRule(**entry) for entry in data]
        return cls(rules=rules)

    # ------------------------------------------------------------------
    # Redis storage
    # ------------------------------------------------------------------

    def save(self, redis_client, key: str) -> None:  # noqa: ANN001
        """Persist the policy to Redis as a JSON string."""
        redis_client.hset(key, "policy", self.to_json())

    @classmethod
    def load(cls, redis_client, key: str) -> ABACPolicy | None:  # noqa: ANN001
        """Load a policy from Redis.  Returns *None* when not found."""
        raw = redis_client.hget(key, "policy")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return cls.from_json(raw)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(rule: ABACRule, subject: dict, resource: dict, action: str) -> bool:
        """Check whether *rule* matches the given request context."""
        # Action match (wildcard or exact)
        if rule.action != "*" and rule.action != action:
            return False
        # Subject attributes — every key/value in the rule must appear in subject
        for k, v in rule.subject_attrs.items():
            if subject.get(k) != v:
                return False
        # Resource attributes — same logic
        for k, v in rule.resource_attrs.items():
            if resource.get(k) != v:
                return False
        return True


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class ABACMiddleware(BaseHTTPMiddleware):
    """Enforce ABAC policy on incoming requests.

    When ``CERID_ENTERPRISE`` is *False* the middleware is a no-op pass-through.
    """

    def __init__(self, app, policy: ABACPolicy | None = None):  # noqa: ANN001
        super().__init__(app)
        self.policy = policy

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        # Deferred import to avoid circular dependency at module level
        from config import settings

        if not getattr(settings, "CERID_ENTERPRISE", False):
            return await call_next(request)

        if self.policy is None:
            return await call_next(request)

        # Subject attrs from request.state (populated by JWT middleware upstream)
        subject: dict = {}
        if hasattr(request.state, "user"):
            user = request.state.user
            subject = user if isinstance(user, dict) else {}

        # Resource attrs from endpoint metadata (route.endpoint.__dict__ or tags)
        resource: dict = {}
        route = request.scope.get("route")
        if route is not None:
            resource = getattr(route, "enterprise_attrs", {})

        action = request.method.lower()

        result = self.policy.evaluate(subject, resource, action)
        if result == "deny":
            logger.warning(
                "ABAC denied: subject=%s resource=%s action=%s",
                subject.get("role", "unknown"),
                resource,
                action,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Access denied by ABAC policy"},
            )

        return await call_next(request)
