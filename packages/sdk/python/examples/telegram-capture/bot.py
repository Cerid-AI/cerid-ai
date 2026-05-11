#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Telegram capture bot — example agent for POST /sdk/v1/ingest/external.

This is a self-contained example that demonstrates the generic external
ingest endpoint.  It is NOT a Cerid feature; it is not published to PyPI.

Run it as:
    TELEGRAM_BOT_TOKEN=<token> CERID_URL=http://localhost:8888 python bot.py

Every message the bot receives is forwarded to Cerid via the generic
ingest endpoint.  The field_mappings translate the Telegram Update JSON
into Cerid's canonical ingest shape.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
CERID_URL: str = os.getenv("CERID_URL", "http://localhost:8888")
CERID_CLIENT_ID: str = os.getenv("CERID_CLIENT_ID", "telegram-capture-bot")
INGEST_ENDPOINT: str = f"{CERID_URL}/sdk/v1/ingest/external"

# ---------------------------------------------------------------------------
# Field mappings — translate Telegram Update JSON to Cerid canonical fields.
#
# Telegram message payload shape (relevant fields):
#   {
#     "message_id": 42,
#     "text": "the message text",
#     "chat": {"id": 123456789, "type": "private", "username": "alice"},
#     "date": 1715333400,   (Unix timestamp)
#   }
#
# We capture:
#   content    ← message text
#   source_uri ← a stable "telegram://chat_id/message_id" URI
#   ts         ← ISO-formatted date is not in the raw payload; we compute it
#                before sending (see _make_payload below)
#   id         ← message_id for dedup
# ---------------------------------------------------------------------------

FIELD_MAPPINGS = {
    "content": "text",
    "source_uri": "source_uri",   # we inject this synthetic field
    "ts": "ts_iso",               # we inject this synthetic field
    "id": "message_id",
}


def _make_payload(update: Update) -> dict:
    """Construct the payload dict sent to the ingest endpoint."""
    msg = update.effective_message
    if msg is None:
        return {}

    chat_id = msg.chat_id
    message_id = msg.message_id
    text = msg.text or msg.caption or ""
    ts_iso = msg.date.isoformat() if msg.date else ""

    return {
        "text": text,
        "message_id": str(message_id),
        "source_uri": f"telegram://{chat_id}/{message_id}",
        "ts_iso": ts_iso,
    }


# ---------------------------------------------------------------------------
# Ingest handler
# ---------------------------------------------------------------------------


async def _ingest_message(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            INGEST_ENDPOINT,
            json={
                "source_type": "telegram-bot",
                "payload": payload,
                "field_mappings": FIELD_MAPPINGS,
            },
            headers={"X-Client-ID": CERID_CLIENT_ID},
        )
        resp.raise_for_status()
        return resp.json()


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = _make_payload(update)
    if not payload.get("text"):
        logger.debug("Skipping message with no text (message_id=%s)", payload.get("message_id"))
        return

    try:
        result = await _ingest_message(payload)
        logger.info(
            "Ingested telegram message — accepted=%s skipped=%s errors=%s",
            result.get("accepted", 0),
            result.get("skipped", 0),
            len(result.get("errors", [])),
        )
    except Exception as exc:
        logger.error("Failed to ingest message: %s", exc)


# ---------------------------------------------------------------------------
# Bot entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, message_handler))
    logger.info("Starting Telegram capture bot → %s", INGEST_ENDPOINT)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
