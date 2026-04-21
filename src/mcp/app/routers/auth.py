# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authentication router for multi-user mode.

Provides registration, login, token refresh, logout, and user profile
endpoints. Only registered when ``CERID_MULTI_USER=true``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.neo4j.users import (
    create_tenant,
    create_user,
    get_tenant,
    get_user_by_email,
    get_user_by_id,
    update_last_login,
)
from app.deps import get_neo4j, get_redis
from app.middleware.jwt_auth import create_access_token, decode_access_token
from app.models.user import UserPublic
from config.features import (
    CERID_JWT_ACCESS_TTL,
    CERID_JWT_REFRESH_TTL,
    DEFAULT_TENANT_ID,
)
from core.utils.swallowed import log_swallowed_error
from utils.encryption import encrypt_field

logger = logging.getLogger("ai-companion.auth")

UTC = timezone.utc

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="Password (min 8 chars)")
    display_name: str = Field("", description="Optional display name")
    tenant_name: str = Field("", description="Tenant name (creates new tenant if set)")


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = CERID_JWT_ACCESS_TTL
    user: UserPublic


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = CERID_JWT_ACCESS_TTL


class ApiKeyRequest(BaseModel):
    api_key: str = Field(..., description="OpenRouter API key")


class ApiKeyStatusResponse(BaseModel):
    has_key: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost factor 12)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _build_access_token(user_id: str, tenant_id: str, role: str) -> str:
    """Build a signed JWT access token."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=CERID_JWT_ACCESS_TTL),
    }
    return create_access_token(payload)


def _build_refresh_token(user_id: str) -> str:
    """Build a signed JWT refresh token with unique jti for revocation."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "jti": uuid.uuid4().hex,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(seconds=CERID_JWT_REFRESH_TTL),
    }
    return create_access_token(payload)


def _store_refresh_token(redis_client, jti: str, user_id: str) -> None:
    """Store a refresh token JTI in Redis with TTL for revocation tracking."""
    key = f"refresh_token:{jti}"
    redis_client.setex(key, CERID_JWT_REFRESH_TTL, user_id)


def _revoke_refresh_token(redis_client, jti: str) -> None:
    """Revoke a refresh token by deleting its JTI from Redis."""
    redis_client.delete(f"refresh_token:{jti}")


def _is_refresh_valid(redis_client, jti: str) -> bool:
    """Check if a refresh token JTI is still valid (not revoked)."""
    return redis_client.exists(f"refresh_token:{jti}") == 1


def _get_authenticated_user(request: Request) -> dict:
    """Extract authenticated user_id from request.state (set by JWTAuthMiddleware)."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_id(get_neo4j(), user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest):
    """Register a new user. Creates a new tenant if tenant_name is provided."""
    driver = get_neo4j()

    # Check for existing user
    existing = get_user_by_email(driver, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Determine tenant
    if body.tenant_name:
        tenant_id = uuid.uuid4().hex
        create_tenant(driver, name=body.tenant_name, tenant_id=tenant_id)
        role = "admin"  # First user of a new tenant is admin
    else:
        tenant_id = DEFAULT_TENANT_ID
        # Ensure default tenant exists
        if not get_tenant(driver, tenant_id):
            create_tenant(driver, name="Default", tenant_id=tenant_id)
        role = "member"

    # Create user
    hashed = _hash_password(body.password)
    user_record = create_user(
        driver,
        email=body.email,
        hashed_password=hashed,
        display_name=body.display_name or body.email.split("@")[0],
        role=role,
        tenant_id=tenant_id,
    )
    user_id = user_record["id"]

    update_last_login(driver, user_id)

    # Issue tokens
    access_token = _build_access_token(user_id, tenant_id, role)
    refresh_token = _build_refresh_token(user_id)

    # Store refresh token JTI in Redis
    refresh_payload = decode_access_token(refresh_token)
    _store_refresh_token(get_redis(), refresh_payload["jti"], user_id)

    user_public = UserPublic(
        id=user_id,
        email=body.email,
        display_name=body.display_name or body.email.split("@")[0],
        role=role,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user_public,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    """Authenticate with email and password, returning access + refresh tokens."""
    driver = get_neo4j()
    user = get_user_by_email(driver, body.email)

    if not user or not _verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    update_last_login(driver, user["id"])

    access_token = _build_access_token(user["id"], user["tenant_id"], user["role"])
    refresh_token = _build_refresh_token(user["id"])

    refresh_payload = decode_access_token(refresh_token)
    _store_refresh_token(get_redis(), refresh_payload["jti"], user["id"])

    user_public = UserPublic(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name", ""),
        role=user["role"],
        tenant_id=user["tenant_id"],
        has_api_key=bool(user.get("openrouter_api_key_encrypted")),
        usage_queries=user.get("usage_queries", 0),
        usage_ingestions=user.get("usage_ingestions", 0),
        created_at=datetime.fromisoformat(user["created_at"]),
        last_login=datetime.now(UTC),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user_public,
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token."""
    try:
        payload = decode_access_token(body.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    jti = payload.get("jti")
    if not jti or not _is_refresh_valid(get_redis(), jti):
        raise HTTPException(status_code=401, detail="Refresh token revoked or expired")

    user = get_user_by_id(get_neo4j(), payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    access_token = _build_access_token(user["id"], user["tenant_id"], user["role"])

    return RefreshResponse(access_token=access_token)


@router.post("/logout")
def logout(body: RefreshRequest):
    """Revoke the refresh token (invalidates future refresh attempts)."""
    try:
        payload = decode_access_token(body.refresh_token)
        jti = payload.get("jti")
        if jti:
            _revoke_refresh_token(get_redis(), jti)
    except Exception as exc:
        # Token already invalid — logout is idempotent. Still count for visibility.
        log_swallowed_error("app.routers.auth.logout_token_revoke", exc)

    return {"detail": "Logged out"}


@router.get("/me", response_model=UserPublic)
def me(request: Request):
    """Return the current authenticated user's profile."""
    user = _get_authenticated_user(request)

    return UserPublic(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name", ""),
        role=user["role"],
        tenant_id=user["tenant_id"],
        has_api_key=bool(user.get("openrouter_api_key_encrypted")),
        usage_queries=user.get("usage_queries", 0),
        usage_ingestions=user.get("usage_ingestions", 0),
        created_at=datetime.fromisoformat(user["created_at"]),
        last_login=(
            datetime.fromisoformat(user["last_login"])
            if user.get("last_login") else None
        ),
    )


# ---------------------------------------------------------------------------
# Per-user API key management
# ---------------------------------------------------------------------------

@router.put("/me/api-key")
def set_api_key(body: ApiKeyRequest, request: Request):
    """Store the user's OpenRouter API key (encrypted at rest)."""
    user = _get_authenticated_user(request)
    driver = get_neo4j()

    encrypted = encrypt_field(body.api_key)

    from app.db.neo4j.users import update_user
    update_user(driver, user["id"], openrouter_api_key_encrypted=encrypted)

    return {"detail": "API key saved"}


@router.delete("/me/api-key")
def delete_api_key(request: Request):
    """Remove the user's stored OpenRouter API key."""
    user = _get_authenticated_user(request)
    driver = get_neo4j()

    from app.db.neo4j.users import update_user
    update_user(driver, user["id"], openrouter_api_key_encrypted="")

    return {"detail": "API key removed"}


@router.get("/me/api-key/status", response_model=ApiKeyStatusResponse)
def api_key_status(request: Request):
    """Check if the user has an OpenRouter API key stored (never returns the key)."""
    user = _get_authenticated_user(request)
    return ApiKeyStatusResponse(
        has_key=bool(user.get("openrouter_api_key_encrypted"))
    )


# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------

@router.get("/me/usage")
def user_usage(request: Request):
    """Return the current month's usage counters for the authenticated user."""
    user = _get_authenticated_user(request)
    from utils.usage import get_usage
    return get_usage(get_redis(), user["id"])
