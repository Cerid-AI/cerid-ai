# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Outlook/Microsoft Graph connector — imports emails into Cerid KB.

This is a **scaffold** — real OAuth flows require app registration in
Azure Active Directory (Entra ID). The complete data flow structure is
implemented with clear TODO markers where actual Microsoft Graph API
calls would go.

Required environment variables:
    CERID_OUTLOOK_CLIENT_ID     — Azure AD (Entra) application client ID
    CERID_OUTLOOK_CLIENT_SECRET — Azure AD application client secret
    CERID_OUTLOOK_TENANT_ID     — Azure AD tenant ID (or "common" for multi-tenant)
    CERID_OUTLOOK_REDIRECT_URI  — OAuth2 callback URL (e.g. http://localhost:8888/plugins/outlook/callback)

Redis keys used:
    cerid:outlook:refresh_token — Encrypted OAuth2 refresh token
    cerid:outlook:delta_link    — Microsoft Graph delta link for incremental sync

Feature gate: outlook_connector (pro tier)
Circuit breaker: "outlook"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai-companion.plugins.outlook")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTLOOK_CLIENT_ID = os.getenv("CERID_OUTLOOK_CLIENT_ID", "")
OUTLOOK_CLIENT_SECRET = os.getenv("CERID_OUTLOOK_CLIENT_SECRET", "")
OUTLOOK_TENANT_ID = os.getenv("CERID_OUTLOOK_TENANT_ID", "common")
OUTLOOK_REDIRECT_URI = os.getenv(
    "CERID_OUTLOOK_REDIRECT_URI",
    "http://localhost:8888/plugins/outlook/callback",
)

# Microsoft Graph scopes — read-only mail access
OUTLOOK_SCOPES = [
    "Mail.Read",
    "offline_access",
]

# Microsoft identity platform endpoints
MS_AUTH_URL = f"https://login.microsoftonline.com/{OUTLOOK_TENANT_ID}/oauth2/v2.0/authorize"
MS_TOKEN_URL = f"https://login.microsoftonline.com/{OUTLOOK_TENANT_ID}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Redis key prefixes
REDIS_KEY_REFRESH_TOKEN = "cerid:outlook:refresh_token"
REDIS_KEY_DELTA_LINK = "cerid:outlook:delta_link"

# Default poll interval (minutes) — overridable via manifest config
DEFAULT_POLL_INTERVAL = 15


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class OutlookMessage:
    """Normalized representation of an Outlook message for ingestion."""

    message_id: str
    conversation_id: str
    subject: str
    sender: str
    recipients: list[str]
    received_date: str
    body_text: str
    body_html: str = ""
    categories: list[str] = field(default_factory=list)
    importance: str = "normal"
    has_attachments: bool = False


# ---------------------------------------------------------------------------
# Outlook Connector
# ---------------------------------------------------------------------------

class OutlookConnector:
    """Outlook/Microsoft Graph connector for incremental email import.

    Uses Microsoft Identity Platform (MSAL pattern) for OAuth2 and
    Microsoft Graph delta queries for efficient incremental sync.

    Lifecycle:
        1. ``start_auth()`` — generate Microsoft OAuth URL, redirect user
        2. ``handle_callback(code)`` — exchange auth code for tokens
        3. ``sync_messages()`` — incremental sync using delta queries
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._validate_config()

    def _validate_config(self) -> None:
        """Warn if required env vars are missing (scaffold-safe)."""
        if not OUTLOOK_CLIENT_ID:
            logger.warning(
                "CERID_OUTLOOK_CLIENT_ID not set — Outlook connector will not function. "
                "Register an app at https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps"
            )
        if not OUTLOOK_CLIENT_SECRET:
            logger.warning(
                "CERID_OUTLOOK_CLIENT_SECRET not set — Outlook connector will not function."
            )

    # ----- Auth Flow -----

    def start_auth(self) -> str:
        """Generate the Microsoft OAuth2 authorization URL.

        Returns:
            The OAuth URL to redirect the user to for consent.
        """
        from config.features import check_feature
        check_feature("outlook_connector")

        params = {
            "client_id": OUTLOOK_CLIENT_ID,
            "redirect_uri": OUTLOOK_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(OUTLOOK_SCOPES),
            "response_mode": "query",
            "prompt": "consent",
        }
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        auth_url = f"{MS_AUTH_URL}?{query_string}"

        logger.info("Outlook OAuth: generated auth URL (redirect_uri=%s)", OUTLOOK_REDIRECT_URI)
        return auth_url

    async def handle_callback(self, code: str) -> dict[str, Any]:
        """Exchange the authorization code for access + refresh tokens.

        Args:
            code: The authorization code from Microsoft's OAuth callback.

        Returns:
            Token metadata (access_token expiry, scopes granted).
        """
        from config.features import check_feature
        check_feature("outlook_connector")

        # TODO: Replace with actual HTTP POST to MS_TOKEN_URL
        # using httpx or MSAL:
        #
        # async with httpx.AsyncClient() as client:
        #     response = await client.post(MS_TOKEN_URL, data={
        #         "code": code,
        #         "client_id": OUTLOOK_CLIENT_ID,
        #         "client_secret": OUTLOOK_CLIENT_SECRET,
        #         "redirect_uri": OUTLOOK_REDIRECT_URI,
        #         "grant_type": "authorization_code",
        #         "scope": " ".join(OUTLOOK_SCOPES),
        #     })
        #     tokens = response.json()
        #
        # Alternative: use msal.ConfidentialClientApplication for PKCE flow

        # Scaffold: simulate token exchange
        tokens = {
            "access_token": f"scaffold_access_{int(time.time())}",
            "refresh_token": f"scaffold_refresh_{int(time.time())}",
            "expires_in": 3600,
            "scope": " ".join(OUTLOOK_SCOPES),
            "token_type": "Bearer",
        }
        logger.info("Outlook OAuth: exchanged code for tokens (scaffold)")

        # Store encrypted refresh token in Redis
        await self._store_refresh_token(tokens["refresh_token"])

        return {
            "status": "authenticated",
            "expires_in": tokens["expires_in"],
            "scopes": tokens["scope"],
        }

    async def _store_refresh_token(self, refresh_token: str) -> None:
        """Store the refresh token encrypted in Redis.

        TODO: Encrypt with CERID_ENCRYPTION_KEY via utils/encryption.py
        before storing. Current scaffold stores plaintext.
        """
        if self._redis:
            # TODO: from utils.encryption import encrypt_value
            # encrypted = encrypt_value(refresh_token)
            await self._redis.set(REDIS_KEY_REFRESH_TOKEN, refresh_token)
            logger.info("Outlook OAuth: stored refresh token in Redis")
        else:
            logger.warning("Outlook OAuth: no Redis client — refresh token not persisted")

    async def _get_access_token(self) -> str | None:
        """Retrieve a valid access token, refreshing if needed.

        TODO: Implement actual token refresh via MS_TOKEN_URL using
        the stored refresh_token, or use MSAL's acquire_token_by_refresh_token().

        Returns:
            Access token string or None if not authenticated.
        """
        if not self._redis:
            logger.warning("Outlook: no Redis client — cannot retrieve tokens")
            return None

        refresh_token = await self._redis.get(REDIS_KEY_REFRESH_TOKEN)
        if not refresh_token:
            logger.warning("Outlook: no refresh token stored — user must re-authenticate")
            return None

        # TODO: POST to MS_TOKEN_URL with grant_type=refresh_token
        # to get a fresh access_token. For now, return scaffold token.
        return f"scaffold_refreshed_{int(time.time())}"

    # ----- Message Sync -----

    async def sync_messages(self) -> dict[str, Any]:
        """Incremental sync of Outlook messages using Microsoft Graph delta queries.

        Flow:
            1. Get last delta link from Redis
            2. If no delta link, do initial full sync (GET .../messages/delta)
            3. If delta link exists, follow it for incremental changes
            4. Fetch new/modified messages from delta response
            5. Normalize each message to IngestPayload
            6. Call ingest_content() for each
            7. Store new deltaLink in Redis for next sync

        Returns:
            Sync summary with counts of ingested/skipped messages.
        """
        from config.features import check_feature
        from core.utils.circuit_breaker import get_breaker

        check_feature("outlook_connector")
        breaker = get_breaker("outlook")

        access_token = await self._get_access_token()
        if not access_token:
            return {"status": "error", "reason": "not_authenticated"}

        # Get last known delta link
        delta_link = None
        if self._redis:
            raw = await self._redis.get(REDIS_KEY_DELTA_LINK)
            if raw:
                delta_link = raw if isinstance(raw, str) else raw.decode()

        if delta_link:
            result = await breaker.call(
                self._incremental_sync, access_token, delta_link
            )
        else:
            result = await breaker.call(
                self._initial_sync, access_token
            )

        return result

    async def _initial_sync(self, access_token: str) -> dict[str, Any]:
        """Full initial sync using Microsoft Graph delta query.

        TODO: Replace with actual Graph API call:
            GET {GRAPH_API_BASE}/me/mailFolders/inbox/messages/delta
                ?$select=subject,from,toRecipients,receivedDateTime,body,categories,importance,hasAttachments
                &$top=50

        Follow @odata.nextLink for pagination, save @odata.deltaLink
        when pagination is exhausted.
        """
        logger.info("Outlook: starting initial delta sync (scaffold)")

        # TODO: Fetch messages via delta query
        # async with httpx.AsyncClient() as client:
        #     url = f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages/delta"
        #     params = {
        #         "$select": "subject,from,toRecipients,receivedDateTime,body,categories,importance,hasAttachments",
        #         "$top": "50",
        #     }
        #     messages = []
        #     delta_link = None
        #
        #     while url:
        #         resp = await client.get(
        #             url,
        #             headers={"Authorization": f"Bearer {access_token}"},
        #             params=params,
        #         )
        #         data = resp.json()
        #         messages.extend(data.get("value", []))
        #         url = data.get("@odata.nextLink")
        #         params = {}  # nextLink includes params
        #         if not url:
        #             delta_link = data.get("@odata.deltaLink")

        # Scaffold: simulate empty response
        messages: list[dict[str, Any]] = []
        ingested = 0
        skipped = 0

        for raw_msg in messages:
            message = self._parse_graph_message(raw_msg)
            if message:
                success = await self._ingest_message(message)
                if success:
                    ingested += 1
                else:
                    skipped += 1

        # TODO: Save actual deltaLink from Graph response
        scaffold_delta_link = f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages/delta?$deltatoken=scaffold_{int(time.time())}"

        if self._redis:
            await self._redis.set(REDIS_KEY_DELTA_LINK, scaffold_delta_link)

        return {
            "status": "ok",
            "sync_type": "initial",
            "ingested": ingested,
            "skipped": skipped,
        }

    async def _incremental_sync(
        self, access_token: str, delta_link: str
    ) -> dict[str, Any]:
        """Incremental sync by following the stored delta link.

        TODO: Replace with actual Graph API call:
            GET {delta_link}

        The delta link returns only changes since the last sync.
        New @odata.deltaLink is returned when all changes are consumed.
        """
        logger.info("Outlook: incremental delta sync (scaffold)")

        # TODO: Follow delta link for incremental changes
        # async with httpx.AsyncClient() as client:
        #     messages = []
        #     url = delta_link
        #     new_delta_link = None
        #
        #     while url:
        #         resp = await client.get(
        #             url,
        #             headers={"Authorization": f"Bearer {access_token}"},
        #         )
        #         data = resp.json()
        #         messages.extend(data.get("value", []))
        #         url = data.get("@odata.nextLink")
        #         if not url:
        #             new_delta_link = data.get("@odata.deltaLink")

        # Scaffold: simulate no new messages
        messages: list[dict[str, Any]] = []
        ingested = 0
        skipped = 0

        for raw_msg in messages:
            # Skip removed messages (Graph delta returns @removed for deletions)
            if "@removed" in raw_msg:
                continue
            message = self._parse_graph_message(raw_msg)
            if message:
                success = await self._ingest_message(message)
                if success:
                    ingested += 1
                else:
                    skipped += 1

        # TODO: Save actual new deltaLink from Graph response
        new_delta_link = f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages/delta?$deltatoken=scaffold_{int(time.time())}"

        if self._redis:
            await self._redis.set(REDIS_KEY_DELTA_LINK, new_delta_link)

        return {
            "status": "ok",
            "sync_type": "incremental",
            "ingested": ingested,
            "skipped": skipped,
        }

    def _parse_graph_message(self, raw: dict[str, Any]) -> OutlookMessage | None:
        """Parse a Microsoft Graph message response into OutlookMessage.

        TODO: This will receive the actual Graph API message object.
        Current scaffold shows the expected field mapping.
        """
        try:
            sender_data = raw.get("from", {}).get("emailAddress", {})
            sender = f"{sender_data.get('name', '')} <{sender_data.get('address', '')}>"

            recipients = []
            for r in raw.get("toRecipients", []):
                addr = r.get("emailAddress", {})
                recipients.append(addr.get("address", ""))

            body = raw.get("body", {})

            return OutlookMessage(
                message_id=raw.get("id", ""),
                conversation_id=raw.get("conversationId", ""),
                subject=raw.get("subject", "(no subject)"),
                sender=sender.strip(),
                recipients=recipients,
                received_date=raw.get("receivedDateTime", ""),
                body_text=body.get("content", "") if body.get("contentType") == "text" else "",
                body_html=body.get("content", "") if body.get("contentType") == "html" else "",
                categories=raw.get("categories", []),
                importance=raw.get("importance", "normal"),
                has_attachments=raw.get("hasAttachments", False),
            )
        except Exception:
            logger.exception("Outlook: failed to parse message %s", raw.get("id", "?"))
            return None

    async def _ingest_message(self, message: OutlookMessage) -> bool:
        """Normalize an Outlook message and ingest into Cerid KB.

        Converts the message to plain text content and calls
        ingest_content() from the ingestion service.

        Returns:
            True if ingested successfully, False if skipped/failed.
        """
        from services.ingestion import ingest_content

        # Build content string from email fields
        content_parts = [
            f"Subject: {message.subject}",
            f"From: {message.sender}",
            f"To: {', '.join(message.recipients)}",
            f"Date: {message.received_date}",
            "",
            message.body_text or "(no text body)",
        ]
        content = "\n".join(content_parts)

        # Deduplicate by Outlook message ID
        content_hash = hashlib.sha256(
            f"outlook:{message.message_id}".encode()
        ).hexdigest()

        metadata: dict[str, Any] = {
            "source": "outlook",
            "source_type": "email",
            "outlook_message_id": message.message_id,
            "outlook_conversation_id": message.conversation_id,
            "subject": message.subject,
            "sender": message.sender,
            "date": message.received_date,
            "categories": json.dumps(message.categories),
            "importance": message.importance,
            "content_hash": content_hash,
        }

        try:
            result = ingest_content(
                content=content,
                domain="general",
                metadata=metadata,
            )
            logger.info(
                "Outlook: ingested message %s (%s)",
                message.message_id,
                message.subject[:50],
            )
            return bool(result)
        except Exception:
            logger.exception(
                "Outlook: failed to ingest message %s", message.message_id
            )
            return False


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register() -> None:
    """Register the Outlook connector plugin.

    Called by the plugin loader when the plugin is enabled.
    Validates configuration and logs readiness state.
    """
    if OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET:
        logger.info(
            "Outlook connector plugin registered (tenant=%s, redirect_uri=%s)",
            OUTLOOK_TENANT_ID,
            OUTLOOK_REDIRECT_URI,
        )
    else:
        logger.info(
            "Outlook connector plugin registered (scaffold mode — "
            "set CERID_OUTLOOK_CLIENT_ID and CERID_OUTLOOK_CLIENT_SECRET to enable)"
        )
