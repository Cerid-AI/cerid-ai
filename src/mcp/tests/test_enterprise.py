# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the enterprise tier modules (ABAC, classification, SSO, audit)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise.abac import ABACPolicy, ABACRule
from enterprise.audit_immutable import audit_log, query_audit_log
from enterprise.classification import (
    ClassificationLevel,
    classify_chunk,
    detect_aggregation_risk,
)
from enterprise.sso import SSOConfig, get_oidc_discovery

# =========================================================================
# ABAC
# =========================================================================


class TestABACPolicy:
    def test_abac_policy_allow(self):
        """Analyst reading an UNCLASSIFIED resource should be allowed."""
        policy = ABACPolicy(rules=[
            ABACRule(
                subject_attrs={"role": "analyst"},
                resource_attrs={"classification": "UNCLASSIFIED"},
                action="get",
                effect="allow",
            ),
        ])
        result = policy.evaluate(
            subject={"role": "analyst"},
            resource={"classification": "UNCLASSIFIED"},
            action="get",
        )
        assert result == "allow"

    def test_abac_policy_deny(self):
        """Analyst reading a SECRET resource should be denied."""
        policy = ABACPolicy(rules=[
            ABACRule(
                subject_attrs={"role": "analyst"},
                resource_attrs={"classification": "SECRET"},
                action="get",
                effect="deny",
            ),
        ])
        result = policy.evaluate(
            subject={"role": "analyst"},
            resource={"classification": "SECRET"},
            action="get",
        )
        assert result == "deny"

    def test_abac_default_deny(self):
        """No matching rule should return deny (default-deny)."""
        policy = ABACPolicy(rules=[
            ABACRule(
                subject_attrs={"role": "admin"},
                resource_attrs={},
                action="*",
                effect="allow",
            ),
        ])
        # Subject is "analyst" but rule requires "admin"
        result = policy.evaluate(
            subject={"role": "analyst"},
            resource={},
            action="get",
        )
        assert result == "deny"

    def test_abac_policy_serialization_roundtrip(self):
        """Policy should survive JSON serialization/deserialization."""
        original = ABACPolicy(rules=[
            ABACRule(subject_attrs={"role": "admin"}, effect="allow"),
            ABACRule(subject_attrs={"role": "guest"}, effect="deny"),
        ])
        raw = original.to_json()
        restored = ABACPolicy.from_json(raw)
        assert len(restored.rules) == 2
        assert restored.rules[0].subject_attrs == {"role": "admin"}
        assert restored.rules[1].effect == "deny"

    def test_abac_redis_persistence(self):
        """Policy save/load via Redis mock."""
        redis = MagicMock()
        store: dict = {}

        def _hset(key, field, value):
            store[(key, field)] = value

        def _hget(key, field):
            return store.get((key, field))

        redis.hset.side_effect = _hset
        redis.hget.side_effect = _hget

        policy = ABACPolicy(rules=[
            ABACRule(subject_attrs={"role": "admin"}, effect="allow"),
        ])
        policy.save(redis, "test:abac")

        loaded = ABACPolicy.load(redis, "test:abac")
        assert loaded is not None
        assert len(loaded.rules) == 1
        assert loaded.rules[0].subject_attrs == {"role": "admin"}

    def test_abac_load_missing(self):
        """Loading from Redis when key is absent returns None."""
        redis = MagicMock()
        redis.hget.return_value = None
        assert ABACPolicy.load(redis, "missing:key") is None


# =========================================================================
# Classification
# =========================================================================


class TestClassification:
    def test_classification_level_ordering(self):
        """Enum values should be ordered from lowest to highest."""
        assert ClassificationLevel.UNCLASSIFIED < ClassificationLevel.CUI
        assert ClassificationLevel.CUI < ClassificationLevel.SECRET
        assert ClassificationLevel.SECRET < ClassificationLevel.TOP_SECRET
        assert ClassificationLevel.TOP_SECRET < ClassificationLevel.TS_SCI

    def test_classify_chunk_default(self):
        """No metadata should return UNCLASSIFIED."""
        assert classify_chunk({}) == ClassificationLevel.UNCLASSIFIED

    def test_classify_chunk_known_level(self):
        """Known classification key maps correctly."""
        assert classify_chunk({"classification": "secret"}) == ClassificationLevel.SECRET
        assert classify_chunk({"classification": "TS_SCI"}) == ClassificationLevel.TS_SCI

    def test_aggregation_risk_detection(self):
        """Mixing UNCLASSIFIED + SECRET chunks should produce a warning."""
        chunks = [
            {"id": "c1", "metadata": {"classification": "unclassified"}},
            {"id": "c2", "metadata": {"classification": "secret"}},
        ]
        warnings = detect_aggregation_risk(chunks)
        assert len(warnings) == 1
        assert "c1" in warnings[0]["chunk_ids"]
        assert "c2" in warnings[0]["chunk_ids"]
        assert "TOP_SECRET" in warnings[0]["suggested_level"]

    def test_aggregation_risk_no_risk(self):
        """All chunks at the same level should produce no warnings."""
        chunks = [
            {"id": "c1", "metadata": {"classification": "secret"}},
            {"id": "c2", "metadata": {"classification": "secret"}},
        ]
        warnings = detect_aggregation_risk(chunks)
        assert warnings == []

    def test_aggregation_risk_empty(self):
        """Empty chunk list produces no warnings."""
        assert detect_aggregation_risk([]) == []


# =========================================================================
# Audit (immutable logging)
# =========================================================================


class TestAuditLog:
    @patch("enterprise.audit_immutable._stream_key", return_value="test:audit:stream")
    def test_audit_log_roundtrip(self, _mock_key):
        """Log an entry, query it back, verify fields."""
        redis = MagicMock()

        # Simulate XADD returning an ID
        redis.xadd.return_value = b"1234567890-0"

        entry_id = audit_log(
            redis,
            event_type="access",
            actor="user@example.com",
            resource="/api/secret",
            action="read",
            result="allowed",
            metadata={"ip": "10.0.0.1"},
        )
        assert entry_id == "1234567890-0"

        # Verify xadd was called with correct stream key and fields
        call_args = redis.xadd.call_args
        assert call_args[0][0] == "test:audit:stream"
        fields = call_args[0][1]
        assert fields["event_type"] == "access"
        assert fields["actor"] == "user@example.com"
        assert fields["action"] == "read"
        assert json.loads(fields["metadata"]) == {"ip": "10.0.0.1"}

        # Now simulate XRANGE returning the entry we just wrote
        redis.xrange.return_value = [
            (b"1234567890-0", {
                b"event_type": b"access",
                b"actor": b"user@example.com",
                b"resource": b"/api/secret",
                b"action": b"read",
                b"result": b"allowed",
                b"timestamp": b"1234567890.0",
                b"metadata": json.dumps({"ip": "10.0.0.1"}).encode(),
            }),
        ]

        results = query_audit_log(redis, event_type="access")
        assert len(results) == 1
        assert results[0]["actor"] == "user@example.com"
        assert results[0]["metadata"] == {"ip": "10.0.0.1"}
        assert results[0]["id"] == "1234567890-0"

    @patch("enterprise.audit_immutable._stream_key", return_value="test:audit:stream")
    def test_audit_log_filtering(self, _mock_key):
        """Filter by event_type and actor should exclude non-matching entries."""
        redis = MagicMock()

        redis.xrange.return_value = [
            (b"1-0", {
                b"event_type": b"access",
                b"actor": b"alice",
                b"resource": b"/a",
                b"action": b"read",
                b"result": b"ok",
                b"timestamp": b"1.0",
            }),
            (b"2-0", {
                b"event_type": b"admin",
                b"actor": b"bob",
                b"resource": b"/b",
                b"action": b"write",
                b"result": b"ok",
                b"timestamp": b"2.0",
            }),
            (b"3-0", {
                b"event_type": b"access",
                b"actor": b"bob",
                b"resource": b"/c",
                b"action": b"read",
                b"result": b"denied",
                b"timestamp": b"3.0",
            }),
        ]

        # Filter: event_type=access, actor=bob → only entry 3
        results = query_audit_log(redis, event_type="access", actor="bob")
        assert len(results) == 1
        assert results[0]["id"] == "3-0"
        assert results[0]["result"] == "denied"


# =========================================================================
# SSO
# =========================================================================


class TestSSO:
    def test_sso_config_creation(self):
        """SSOConfig dataclass should accept all fields."""
        config = SSOConfig(
            provider="oidc",
            metadata_url="https://idp.example.com/.well-known/openid-configuration",
            client_id="my-client",
            client_secret="s3cret",  # pragma: allowlist secret
            attribute_mapping={"email": "preferred_username"},
        )
        assert config.provider == "oidc"
        assert config.client_id == "my-client"
        assert config.attribute_mapping["email"] == "preferred_username"

    @pytest.mark.asyncio
    async def test_oidc_discovery(self):
        """Mock httpx call to .well-known endpoint."""
        fake_discovery = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
        }

        mock_response = MagicMock()
        mock_response.json.return_value = fake_discovery
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # httpx is imported inside the function, so we patch it in sys.modules
        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            result = await get_oidc_discovery("https://idp.example.com/.well-known/openid-configuration")

        assert result["issuer"] == "https://idp.example.com"
        assert result["token_endpoint"] == "https://idp.example.com/token"

    def test_saml_not_implemented(self):
        """SAML validation should raise NotImplementedError."""
        from enterprise.sso import validate_saml_assertion

        with pytest.raises(NotImplementedError, match="xmlsec"):
            validate_saml_assertion("<xml>test</xml>")
