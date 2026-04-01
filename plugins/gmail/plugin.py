# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Gmail OAuth connector — imports emails into Cerid KB via Google API.

This is a **scaffold** — real OAuth flows require app registration with Google
Cloud Console. The complete data flow structure is implemented with clear TODO
markers where actual Google API calls would go.

Required environment variables:
    CERID_GMAIL_CLIENT_ID       — Google OAuth2 client ID
    CERID_GMAIL_CLIENT_SECRET   — Google OAuth2 client secret
    CERID_GMAIL_REDIRECT_URI    — OAuth2 callback URL (e.g. http://localhost:8888/plugins/gmail/callback)

Redis keys used:
    cerid:gmail:refresh_token   — Encrypted OAuth2 refresh token
    cerid:gmail:history_id      — Last synced Gmail historyId for incremental sync

Feature gate: gmail_connector (pro tier)
Circuit breaker: "gmail"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai-companion.plugins.gmail")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GMAIL_CLIENT_ID = os.getenv("CERID_GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("CERID_GMAIL_CLIENT_SECRET", "")
GMAIL_REDIRECT_URI = os.getenv(
    "CERID_GMAIL_REDIRECT_URI",
    "http://localhost:8888/plugins/gmail/callback",
)

# Gmail API scopes — read-only access to messages
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Redis key prefixes
REDIS_KEY_REFRESH_TOKEN = "cerid:gmail:refresh_token"
REDIS_KEY_HISTORY_ID = "cerid:gmail:history_id"

# Default poll interval (minutes) — overridable via manifest config
DEFAULT_POLL_INTERVAL = 15


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GmailMessage:
    """Normalized representation of a Gmail message for ingestion."""

    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipients: list[str]
    date: str
    body_text: str
    body_html: str = ""
    labels: list[str] = field(default_factory=list)
    attachments: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gmail Connector
# ---------------------------------------------------------------------------

class GmailConnector:
    """Gmail OAuth2 connector for incremental email import into Cerid KB.

    Lifecycle:
        1. ``start_auth()`` — generate Google OAuth URL, redirect user
        2. ``handle_callback(code)`` — exchange auth code for tokens
        3. ``sync_messages()`` — incremental sync using historyId
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._validate_config()

    def _validate_config(self) -> None:
        """Warn if required env vars are missing (scaffold-safe)."""
        if not GMAIL_CLIENT_ID:
            logger.warning(
                "CERID_GMAIL_CLIENT_ID not set — Gmail connector will not function. "
                "Register an OAuth app at https://console.cloud.google.com/apis/credentials"
            )
        if not GMAIL_CLIENT_SECRET:
            logger.warning(
                "CERID_GMAIL_CLIENT_SECRET not set — Gmail connector will not function."
            )

    # ----- Auth Flow -----

    def start_auth(self) -> str:
        """Generate the Google OAuth2 authorization URL.

        Returns:
            The OAuth URL to redirect the user to for consent.
        """
        from config.features import check_feature
        check_feature("gmail_connector")

        params = {
            "client_id": GMAIL_CLIENT_ID,
            "redirect_uri": GMAIL_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        auth_url = f"{GOOGLE_AUTH_URL}?{query_string}"

        logger.info("Gmail OAuth: generated auth URL (redirect_uri=%s)", GMAIL_REDIRECT_URI)
        return auth_url

    async def handle_callback(self, code: str) -> dict[str, Any]:
        """Exchange the authorization code for access + refresh tokens.

        Args:
            code: The authorization code from Google's OAuth callback.

        Returns:
            Token metadata (access_token expiry, scopes granted).
        """
        from config.features import check_feature
        check_feature("gmail_connector")

        # TODO: Replace with actual HTTP POST to GOOGLE_TOKEN_URL
        # using httpx or aiohttp:
        #
        # async with httpx.AsyncClient() as client:
        #     response = await client.post(GOOGLE_TOKEN_URL, data={
        #         "code": code,
        #         "client_id": GMAIL_CLIENT_ID,
        #         "client_secret": GMAIL_CLIENT_SECRET,
        #         "redirect_uri": GMAIL_REDIRECT_URI,
        #         "grant_type": "authorization_code",
        #     })
        #     tokens = response.json()

        # Scaffold: simulate token exchange
        tokens = {
            "access_token": f"scaffold_access_{int(time.time())}",
            "refresh_token": f"scaffold_refresh_{int(time.time())}",
            "expires_in": 3600,
            "scope": " ".join(GMAIL_SCOPES),
            "token_type": "Bearer",
        }
        logger.info("Gmail OAuth: exchanged code for tokens (scaffold)")

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
            logger.info("Gmail OAuth: stored refresh token in Redis")
        else:
            logger.warning("Gmail OAuth: no Redis client — refresh token not persisted")

    async def _get_access_token(self) -> str | None:
        """Retrieve a valid access token, refreshing if needed.

        TODO: Implement actual token refresh via GOOGLE_TOKEN_URL using
        the stored refresh_token.

        Returns:
            Access token string or None if not authenticated.
        """
        if not self._redis:
            logger.warning("Gmail: no Redis client — cannot retrieve tokens")
            return None

        refresh_token = await self._redis.get(REDIS_KEY_REFRESH_TOKEN)
        if not refresh_token:
            logger.warning("Gmail: no refresh token stored — user must re-authenticate")
            return None

        # TODO: POST to GOOGLE_TOKEN_URL with grant_type=refresh_token
        # to get a fresh access_token. For now, return scaffold token.
        return f"scaffold_refreshed_{int(time.time())}"

    # ----- Message Sync -----

    async def sync_messages(self) -> dict[str, Any]:
        """Incremental sync of Gmail messages using historyId.

        Flow:
            1. Get last historyId from Redis
            2. If no historyId, do initial full sync (limited to recent messages)
            3. If historyId exists, use Gmail History API for incremental changes
            4. Fetch new/modified messages
            5. Normalize each message to IngestPayload
            6. Call ingest_content() for each
            7. Update historyId in Redis

        Returns:
            Sync summary with counts of ingested/skipped messages.
        """
        from config.features import check_feature
        from utils.circuit_breaker import get_breaker

        check_feature("gmail_connector")
        breaker = get_breaker("gmail")

        access_token = await self._get_access_token()
        if not access_token:
            return {"status": "error", "reason": "not_authenticated"}

        # Get last known historyId
        last_history_id = None
        if self._redis:
            raw = await self._redis.get(REDIS_KEY_HISTORY_ID)
            if raw:
                last_history_id = raw if isinstance(raw, str) else raw.decode()

        if last_history_id:
            result = await breaker.call(
                self._incremental_sync, access_token, last_history_id
            )
        else:
            result = await breaker.call(
                self._initial_sync, access_token
            )

        return result

    async def _initial_sync(self, access_token: str) -> dict[str, Any]:
        """Full initial sync — fetch recent messages from inbox.

        TODO: Replace with actual Gmail API call:
            GET {GMAIL_API_BASE}/users/me/messages?maxResults=100&labelIds=INBOX

        Then for each message ID:
            GET {GMAIL_API_BASE}/users/me/messages/{id}?format=full
        """
        logger.info("Gmail: starting initial sync (scaffold)")

        # TODO: Fetch message list from Gmail API
        # async with httpx.AsyncClient() as client:
        #     resp = await client.get(
        #         f"{GMAIL_API_BASE}/users/me/messages",
        #         headers={"Authorization": f"Bearer {access_token}"},
        #         params={"maxResults": 100, "labelIds": "INBOX"},
        #     )
        #     data = resp.json()
        #     message_ids = [m["id"] for m in data.get("messages", [])]

        # Scaffold: simulate empty message list
        message_ids: list[str] = []
        ingested = 0
        skipped = 0

        for msg_id in message_ids:
            message = await self._fetch_message(access_token, msg_id)
            if message:
                success = await self._ingest_message(message)
                if success:
                    ingested += 1
                else:
                    skipped += 1

        # TODO: Extract historyId from the profile response:
        #     GET {GMAIL_API_BASE}/users/me/profile
        #     new_history_id = profile_data["historyId"]
        new_history_id = str(int(time.time()))

        if self._redis:
            await self._redis.set(REDIS_KEY_HISTORY_ID, new_history_id)

        return {
            "status": "ok",
            "sync_type": "initial",
            "ingested": ingested,
            "skipped": skipped,
            "history_id": new_history_id,
        }

    async def _incremental_sync(
        self, access_token: str, last_history_id: str
    ) -> dict[str, Any]:
        """Incremental sync using Gmail History API.

        TODO: Replace with actual Gmail API call:
            GET {GMAIL_API_BASE}/users/me/history
                ?startHistoryId={last_history_id}
                &historyTypes=messageAdded
                &labelId=INBOX
        """
        logger.info(
            "Gmail: incremental sync from historyId=%s (scaffold)",
            last_history_id,
        )

        # TODO: Fetch history records from Gmail API
        # async with httpx.AsyncClient() as client:
        #     resp = await client.get(
        #         f"{GMAIL_API_BASE}/users/me/history",
        #         headers={"Authorization": f"Bearer {access_token}"},
        #         params={
        #             "startHistoryId": last_history_id,
        #             "historyTypes": "messageAdded",
        #             "labelId": "INBOX",
        #         },
        #     )
        #     data = resp.json()
        #     new_message_ids = set()
        #     for record in data.get("history", []):
        #         for msg in record.get("messagesAdded", []):
        #             new_message_ids.add(msg["message"]["id"])

        # Scaffold: simulate no new messages
        new_message_ids: set[str] = set()
        ingested = 0
        skipped = 0

        for msg_id in new_message_ids:
            message = await self._fetch_message(access_token, msg_id)
            if message:
                success = await self._ingest_message(message)
                if success:
                    ingested += 1
                else:
                    skipped += 1

        # TODO: Use historyId from the history response
        new_history_id = str(int(time.time()))

        if self._redis:
            await self._redis.set(REDIS_KEY_HISTORY_ID, new_history_id)

        return {
            "status": "ok",
            "sync_type": "incremental",
            "ingested": ingested,
            "skipped": skipped,
            "history_id": new_history_id,
            "previous_history_id": last_history_id,
        }

    async def _fetch_message(
        self, access_token: str, message_id: str
    ) -> GmailMessage | None:
        """Fetch and parse a single Gmail message by ID.

        TODO: Replace with actual Gmail API call:
            GET {GMAIL_API_BASE}/users/me/messages/{message_id}?format=full

        Then parse headers (Subject, From, To, Date) and body parts
        (text/plain, text/html) from the response payload.
        """
        logger.debug("Gmail: fetching message %s (scaffold)", message_id)

        # TODO: Actual implementation:
        # async with httpx.AsyncClient() as client:
        #     resp = await client.get(
        #         f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
        #         headers={"Authorization": f"Bearer {access_token}"},
        #         params={"format": "full"},
        #     )
        #     data = resp.json()
        #     headers = {h["name"]: h["value"] for h in data["payload"]["headers"]}
        #     body_text = _extract_body(data["payload"], "text/plain")
        #     body_html = _extract_body(data["payload"], "text/html")
        #     return GmailMessage(
        #         message_id=data["id"],
        #         thread_id=data["threadId"],
        #         subject=headers.get("Subject", "(no subject)"),
        #         sender=headers.get("From", ""),
        #         recipients=_parse_recipients(headers.get("To", "")),
        #         date=headers.get("Date", ""),
        #         body_text=body_text,
        #         body_html=body_html,
        #         labels=data.get("labelIds", []),
        #     )

        return None  # Scaffold: no actual API call

    async def _ingest_message(self, message: GmailMessage) -> bool:
        """Normalize a Gmail message and ingest into Cerid KB.

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
            f"Date: {message.date}",
            "",
            message.body_text or "(no text body)",
        ]
        content = "\n".join(content_parts)

        # Deduplicate by Gmail message ID
        content_hash = hashlib.sha256(
            f"gmail:{message.message_id}".encode()
        ).hexdigest()

        metadata: dict[str, Any] = {
            "source": "gmail",
            "source_type": "email",
            "gmail_message_id": message.message_id,
            "gmail_thread_id": message.thread_id,
            "subject": message.subject,
            "sender": message.sender,
            "date": message.date,
            "labels": json.dumps(message.labels),
            "content_hash": content_hash,
        }

        try:
            result = ingest_content(
                content=content,
                domain="general",
                metadata=metadata,
            )
            logger.info(
                "Gmail: ingested message %s (%s)",
                message.message_id,
                message.subject[:50],
            )
            return bool(result)
        except Exception:
            logger.exception(
                "Gmail: failed to ingest message %s", message.message_id
            )
            return False


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register() -> None:
    """Register the Gmail connector plugin.

    Called by the plugin loader when the plugin is enabled.
    Validates configuration and logs readiness state.
    """
    if GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET:
        logger.info(
            "Gmail connector plugin registered (redirect_uri=%s)",
            GMAIL_REDIRECT_URI,
        )
    else:
        logger.info(
            "Gmail connector plugin registered (scaffold mode — "
            "set CERID_GMAIL_CLIENT_ID and CERID_GMAIL_CLIENT_SECRET to enable)"
        )
