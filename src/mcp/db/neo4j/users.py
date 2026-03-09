# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j CRUD operations for User and Tenant nodes."""
from __future__ import annotations

import logging
import uuid

from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.users")


# ---------------------------------------------------------------------------
# Tenant operations
# ---------------------------------------------------------------------------

def create_tenant(driver, *, name: str, tenant_id: str | None = None) -> dict:
    """Create a new Tenant node. Returns the created tenant dict."""
    tid = tenant_id or str(uuid.uuid4())
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "CREATE (t:Tenant {id: $id, name: $name, created_at: $now}) "
            "RETURN t {.*} AS tenant",
            id=tid, name=name, now=now,
        )
        return result.single()["tenant"]


def get_tenant(driver, tenant_id: str) -> dict | None:
    """Retrieve a tenant by ID."""
    with driver.session() as session:
        result = session.run(
            "MATCH (t:Tenant {id: $id}) RETURN t {.*} AS tenant",
            id=tenant_id,
        )
        record = result.single()
        return record["tenant"] if record else None


def ensure_default_tenant(driver, default_id: str) -> dict:
    """Create default tenant if it doesn't exist (idempotent)."""
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "MERGE (t:Tenant {id: $id}) "
            "ON CREATE SET t.name = 'Default', t.created_at = $now "
            "RETURN t {.*} AS tenant",
            id=default_id, now=now,
        )
        return result.single()["tenant"]


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

def create_user(
    driver,
    *,
    email: str,
    hashed_password: str,
    tenant_id: str,
    display_name: str = "",
    role: str = "member",
) -> dict:
    """Create a new User node linked to a Tenant via MEMBER_OF."""
    uid = str(uuid.uuid4())
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "MATCH (t:Tenant {id: $tenant_id}) "
            "CREATE (u:User {"
            "  id: $id, email: $email, hashed_password: $hashed_password,"
            "  display_name: $display_name, role: $role, tenant_id: $tenant_id,"
            "  usage_queries: 0, usage_ingestions: 0,"
            "  created_at: $now, updated_at: $now"
            "}) "
            "CREATE (u)-[:MEMBER_OF]->(t) "
            "RETURN u {.id, .email, .display_name, .role, .tenant_id, "
            "  .usage_queries, .usage_ingestions, .created_at, .updated_at} AS user",
            id=uid,
            email=email,
            hashed_password=hashed_password,
            display_name=display_name,
            role=role,
            tenant_id=tenant_id,
            now=now,
        )
        record = result.single()
        if not record:
            raise ValueError(f"Tenant {tenant_id} does not exist")
        return record["user"]


def get_user_by_email(driver, email: str) -> dict | None:
    """Retrieve a user by email (for login). Includes hashed_password."""
    with driver.session() as session:
        result = session.run(
            "MATCH (u:User {email: $email}) "
            "RETURN u {.*} AS user",
            email=email,
        )
        record = result.single()
        return record["user"] if record else None


def get_user_by_id(driver, user_id: str) -> dict | None:
    """Retrieve a user by ID. Includes hashed_password."""
    with driver.session() as session:
        result = session.run(
            "MATCH (u:User {id: $id}) "
            "RETURN u {.*} AS user",
            id=user_id,
        )
        record = result.single()
        return record["user"] if record else None


def update_user(driver, user_id: str, **updates) -> dict | None:
    """Update user fields. Returns updated user or None if not found."""
    if not updates:
        return get_user_by_id(driver, user_id)

    set_clauses = ", ".join(f"u.{k} = ${k}" for k in updates)
    updates["updated_at"] = utcnow_iso()
    set_clauses += ", u.updated_at = $updated_at"

    with driver.session() as session:
        result = session.run(
            f"MATCH (u:User {{id: $user_id}}) "
            f"SET {set_clauses} "
            "RETURN u {.*} AS user",
            user_id=user_id,
            **updates,
        )
        record = result.single()
        return record["user"] if record else None


def update_last_login(driver, user_id: str) -> None:
    """Stamp last_login on a user."""
    now = utcnow_iso()
    with driver.session() as session:
        session.run(
            "MATCH (u:User {id: $id}) SET u.last_login = $now",
            id=user_id, now=now,
        )


def list_users(driver, tenant_id: str) -> list[dict]:
    """List all users in a tenant (safe fields only)."""
    with driver.session() as session:
        result = session.run(
            "MATCH (u:User {tenant_id: $tenant_id}) "
            "RETURN u {.id, .email, .display_name, .role, .tenant_id, "
            "  .usage_queries, .usage_ingestions, .created_at, .last_login} AS user "
            "ORDER BY u.created_at",
            tenant_id=tenant_id,
        )
        return [record["user"] for record in result]


def update_usage_counters(
    driver, user_id: str, *, queries: int = 0, ingestions: int = 0
) -> None:
    """Atomically increment usage counters."""
    with driver.session() as session:
        session.run(
            "MATCH (u:User {id: $id}) "
            "SET u.usage_queries = u.usage_queries + $q, "
            "    u.usage_ingestions = u.usage_ingestions + $i",
            id=user_id, q=queries, i=ingestions,
        )
