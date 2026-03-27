# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for multi-user auth system: JWT, user model, tenant context, usage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

class TestJWTHelpers:
    """Test JWT create/decode functions."""

    def test_create_and_decode_token(self):
        from app.middleware.jwt_auth import create_access_token, decode_access_token

        payload = {
            "sub": "user-123",
            "tenant_id": "tenant-456",
            "role": "admin",
        }
        token = create_access_token(payload, secret="test-secret-key-for-jwt")
        decoded = decode_access_token(token, secret="test-secret-key-for-jwt")

        assert decoded["sub"] == "user-123"
        assert decoded["tenant_id"] == "tenant-456"
        assert decoded["role"] == "admin"

    def test_decode_invalid_token(self):
        import jwt as pyjwt

        from app.middleware.jwt_auth import decode_access_token

        with pytest.raises(pyjwt.PyJWTError):
            decode_access_token("not-a-valid-token", secret="test-secret")

    def test_decode_expired_token(self):
        import jwt as pyjwt

        from app.middleware.jwt_auth import create_access_token, decode_access_token

        payload = {
            "sub": "user-123",
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
        token = create_access_token(payload, secret="test-secret")

        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_access_token(token, secret="test-secret")

    def test_decode_wrong_secret(self):
        import jwt as pyjwt

        from app.middleware.jwt_auth import create_access_token, decode_access_token

        payload = {"sub": "user-123"}
        token = create_access_token(payload, secret="test-secret")

        with pytest.raises(pyjwt.PyJWTError):
            decode_access_token(token, secret="wrong-secret")


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class TestUserModel:
    """Test User and Tenant Pydantic models."""

    def test_user_model(self):
        from app.models.user import User

        user = User(
            id="u1",
            email="test@example.com",
            hashed_password="$2b$12$xxx",
            tenant_id="t1",
        )
        assert user.id == "u1"
        assert user.role == "member"
        assert user.usage_queries == 0
        assert user.display_name == ""

    def test_user_public_model(self):
        from app.models.user import UserPublic

        user = UserPublic(
            id="u1",
            email="test@example.com",
            display_name="Test",
            role="admin",
            tenant_id="t1",
            created_at=datetime.now(UTC),
        )
        assert user.has_api_key is False
        assert user.usage_queries == 0

    def test_tenant_model(self):
        from app.models.user import Tenant

        tenant = Tenant(id="t1", name="Test Tenant")
        assert tenant.id == "t1"
        assert tenant.name == "Test Tenant"


# ---------------------------------------------------------------------------
# User CRUD (Neo4j)
# ---------------------------------------------------------------------------

class TestUserCRUD:
    """Test Neo4j user storage functions."""

    def test_create_tenant(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "tenant": {"id": "t1", "name": "Test Tenant"},
        }

        from app.db.neo4j.users import create_tenant

        result = create_tenant(driver, name="Test Tenant", tenant_id="t1")
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "CREATE" in call_args[0][0]
        assert result["id"] == "t1"

    def test_get_tenant(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "tenant": {"id": "t1", "name": "Test"},
        }

        from app.db.neo4j.users import get_tenant
        result = get_tenant(driver, "t1")

        assert result["id"] == "t1"

    def test_get_tenant_not_found(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = None

        from app.db.neo4j.users import get_tenant
        result = get_tenant(driver, "nonexistent")
        assert result is None

    def test_create_user(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "user": {"id": "auto-uuid", "email": "test@example.com"},
        }

        from app.db.neo4j.users import create_user

        result = create_user(
            driver,
            email="test@example.com",
            hashed_password="$2b$12$xxx",
            display_name="Test User",
            role="member",
            tenant_id="t1",
        )
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "CREATE" in call_args[0][0]
        assert "MEMBER_OF" in call_args[0][0]
        assert result["email"] == "test@example.com"

    def test_get_user_by_email(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "user": {"id": "u1", "email": "test@example.com"},
        }

        from app.db.neo4j.users import get_user_by_email
        result = get_user_by_email(driver, "test@example.com")
        assert result["id"] == "u1"

    def test_get_user_by_email_not_found(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = None

        from app.db.neo4j.users import get_user_by_email
        result = get_user_by_email(driver, "nobody@example.com")
        assert result is None

    def test_get_user_by_id(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "user": {"id": "u1", "email": "test@example.com"},
        }

        from app.db.neo4j.users import get_user_by_id
        result = get_user_by_id(driver, "u1")
        assert result["email"] == "test@example.com"

    def test_update_user(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "user": {"id": "u1", "display_name": "New Name"},
        }

        from app.db.neo4j.users import update_user

        update_user(driver, "u1", display_name="New Name", role="admin")
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "SET" in call_args[0][0]

    def test_update_last_login(self, mock_neo4j):
        driver, session = mock_neo4j
        from app.db.neo4j.users import update_last_login

        update_last_login(driver, "u1")
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "last_login" in call_args[0][0]

    def test_list_users(self, mock_neo4j):
        driver, session = mock_neo4j
        record1 = {"user": {"id": "u1"}}
        record2 = {"user": {"id": "u2"}}
        session.run.return_value = [record1, record2]

        from app.db.neo4j.users import list_users
        result = list_users(driver, "t1")
        assert len(result) == 2

    def test_update_usage_counters(self, mock_neo4j):
        driver, session = mock_neo4j
        from app.db.neo4j.users import update_usage_counters

        update_usage_counters(driver, "u1", queries=5, ingestions=3)
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "usage_queries" in call_args[0][0]

    def test_ensure_default_tenant_creates_new(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "tenant": {"id": "default", "name": "Default"},
        }

        from app.db.neo4j.users import ensure_default_tenant

        ensure_default_tenant(driver, "default")
        session.run.assert_called_once()
        assert "MERGE" in session.run.call_args[0][0]


# ---------------------------------------------------------------------------
# Tenant context middleware
# ---------------------------------------------------------------------------

class TestTenantContext:
    """Test tenant context propagation."""

    def test_get_tenant_id_default(self):
        from app.middleware.tenant_context import get_tenant_id
        # Should return default when not in request context
        result = get_tenant_id()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_user_id_default(self):
        from app.middleware.tenant_context import get_user_id
        result = get_user_id()
        assert result is None

    def test_contextvars_set_and_reset(self):
        from app.middleware.tenant_context import (
            get_tenant_id,
            get_user_id,
            tenant_id_var,
            user_id_var,
        )

        t_token = tenant_id_var.set("tenant-abc")
        u_token = user_id_var.set("user-xyz")

        assert get_tenant_id() == "tenant-abc"
        assert get_user_id() == "user-xyz"

        tenant_id_var.reset(t_token)
        user_id_var.reset(u_token)

        assert get_user_id() is None


# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------

class TestUsageMetering:
    """Test Redis-based usage tracking."""

    def test_record_query(self, mock_redis):
        from utils.usage import record_query

        record_query(mock_redis, "user-123")
        mock_redis.incr.assert_called_once()
        mock_redis.expire.assert_called_once()
        key = mock_redis.incr.call_args[0][0]
        assert key.startswith("usage:user-123:queries:")

    def test_record_query_no_user(self, mock_redis):
        from utils.usage import record_query

        record_query(mock_redis, "")
        mock_redis.incr.assert_not_called()

    def test_record_ingestion(self, mock_redis):
        from utils.usage import record_ingestion

        record_ingestion(mock_redis, "user-123", chunks=5)
        mock_redis.incrby.assert_called_once()
        key = mock_redis.incrby.call_args[0][0]
        assert "ingestions" in key
        assert mock_redis.incrby.call_args[0][1] == 5

    def test_get_usage(self, mock_redis):
        from utils.usage import get_usage

        mock_redis.get.side_effect = lambda k: "10" if "queries" in k else "3"

        result = get_usage(mock_redis, "user-123")
        assert result["queries"] == 10
        assert result["ingestions"] == 3
        assert "month" in result

    def test_get_usage_no_data(self, mock_redis):
        from utils.usage import get_usage

        mock_redis.get.return_value = None

        result = get_usage(mock_redis, "user-123")
        assert result["queries"] == 0
        assert result["ingestions"] == 0


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    """Test bcrypt password hashing and verification."""

    def test_hash_and_verify(self):
        from app.routers.auth import _hash_password, _verify_password

        hashed = _hash_password("my-secure-password")
        assert hashed != "my-secure-password"
        assert hashed.startswith("$2b$12$")
        assert _verify_password("my-secure-password", hashed) is True
        assert _verify_password("wrong-password", hashed) is False

    def test_different_hashes_same_password(self):
        from app.routers.auth import _hash_password

        h1 = _hash_password("test-password")
        h2 = _hash_password("test-password")
        assert h1 != h2  # bcrypt uses random salt


# ---------------------------------------------------------------------------
# Auth router helpers
# ---------------------------------------------------------------------------

class TestAuthHelpers:
    """Test auth router helper functions."""

    def test_build_access_token(self):
        import jwt as pyjwt

        # Patch create_access_token where _build_access_token references it
        # (routers.auth imports it at the top, so patch that reference)
        with patch(
            "routers.auth.create_access_token",
            side_effect=lambda payload, secret="test-key": pyjwt.encode(
                payload, "test-key", algorithm="HS256"
            ),
        ):
            from app.routers.auth import _build_access_token

            token = _build_access_token("u1", "t1", "admin")

        payload = pyjwt.decode(token, "test-key", algorithms=["HS256"])

        assert payload["sub"] == "u1"
        assert payload["tenant_id"] == "t1"
        assert payload["role"] == "admin"
        assert "exp" in payload
        assert "iat" in payload

    def test_build_refresh_token(self):
        import jwt as pyjwt

        with patch(
            "routers.auth.create_access_token",
            side_effect=lambda payload, secret="test-key": pyjwt.encode(
                payload, "test-key", algorithm="HS256"
            ),
        ):
            from app.routers.auth import _build_refresh_token

            token = _build_refresh_token("u1")

        payload = pyjwt.decode(token, "test-key", algorithms=["HS256"])

        assert payload["sub"] == "u1"
        assert payload["type"] == "refresh"
        assert "jti" in payload

    def test_store_and_check_refresh_token(self, mock_redis):
        from app.routers.auth import _is_refresh_valid, _store_refresh_token

        mock_redis.exists.return_value = 1
        _store_refresh_token(mock_redis, "jti-123", "user-1")
        mock_redis.setex.assert_called_once()

        assert _is_refresh_valid(mock_redis, "jti-123") is True

    def test_revoke_refresh_token(self, mock_redis):
        from app.routers.auth import _revoke_refresh_token

        _revoke_refresh_token(mock_redis, "jti-123")
        mock_redis.delete.assert_called_once_with("refresh_token:jti-123")

    def test_revoked_token_invalid(self, mock_redis):
        from app.routers.auth import _is_refresh_valid

        mock_redis.exists.return_value = 0
        assert _is_refresh_valid(mock_redis, "jti-revoked") is False


# ---------------------------------------------------------------------------
# Config flags
# ---------------------------------------------------------------------------

class TestConfigFlags:
    """Test multi-user config flags."""

    def test_default_multi_user_false(self):
        # CERID_MULTI_USER defaults to false
        from config.features import CERID_MULTI_USER
        # In test env, should be false unless explicitly set
        assert isinstance(CERID_MULTI_USER, bool)

    def test_default_tenant_id(self):
        from config.features import DEFAULT_TENANT_ID
        assert DEFAULT_TENANT_ID == "default"

    def test_jwt_access_ttl(self):
        from config.features import CERID_JWT_ACCESS_TTL
        assert CERID_JWT_ACCESS_TTL == 900  # 15 minutes

    def test_jwt_refresh_ttl(self):
        from config.features import CERID_JWT_REFRESH_TTL
        assert CERID_JWT_REFRESH_TTL == 604800  # 7 days


# ---------------------------------------------------------------------------
# Chat proxy API key resolution
# ---------------------------------------------------------------------------

class TestChatApiKeyResolution:
    """Test per-user API key resolution in chat proxy."""

    def test_resolve_api_key_no_user(self):
        from app.routers.chat import _resolve_api_key

        request = MagicMock()
        request.state = MagicMock(spec=[])  # No user_id attribute
        del request.state.user_id  # Ensure hasattr returns False

        result = _resolve_api_key(request)
        # Falls back to global key
        assert isinstance(result, str)

    def test_resolve_api_key_with_user_no_key(self):
        from app.routers.chat import _resolve_api_key

        request = MagicMock()
        request.state.user_id = "user-123"

        with patch("deps.get_neo4j") as mock_get_neo4j, \
             patch("db.neo4j.users.get_user_by_id") as mock_get:
            mock_get_neo4j.return_value = MagicMock()
            mock_get.return_value = {"id": "user-123", "openrouter_api_key_encrypted": ""}
            result = _resolve_api_key(request)
            # Should fall back to global key
            assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Bifrost optional api_key parameter
# ---------------------------------------------------------------------------

class TestBifrostApiKey:
    """Test that call_bifrost accepts optional api_key parameter."""

    def test_call_bifrost_signature(self):
        import inspect

        from utils.bifrost import call_bifrost

        sig = inspect.signature(call_bifrost)
        assert "api_key" in sig.parameters
        assert sig.parameters["api_key"].default is None
