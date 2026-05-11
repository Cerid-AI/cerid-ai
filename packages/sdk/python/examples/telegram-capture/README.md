# Telegram Capture Bot — External Ingest Example

A minimal Telegram bot that forwards every received message to Cerid via
`POST /sdk/v1/ingest/external`.  This is a self-contained example that
demonstrates the generic external ingest endpoint — it is **not** a Cerid
feature and is **not** published to PyPI.

---

## Prerequisites

- Python 3.11+
- A running Cerid stack at `http://localhost:8888` (or override via `CERID_URL`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Setup

```bash
# 1. Create a dedicated venv (do NOT install into Cerid's .venv)
cd packages/sdk/python/examples/telegram-capture
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Export environment variables
export TELEGRAM_BOT_TOKEN="<your-bot-token>"
export CERID_URL="http://localhost:8888"        # default
export CERID_CLIENT_ID="telegram-capture-bot"  # optional

# 3. Run
.venv/bin/python bot.py
```

---

## How it works

Each Telegram message is translated into a Cerid `ExternalIngestRequest`
using this `field_mappings` config:

```json
{
  "content": "text",
  "source_uri": "source_uri",
  "ts": "ts_iso",
  "id": "message_id"
}
```

The bot injects two synthetic fields (`source_uri`, `ts_iso`) into the
payload before sending so they resolve cleanly through the dotted-path
mapper.  The resulting `NormalizedItem` is ingested into Cerid's `general`
domain with `source_type="telegram-bot"` stored as provenance metadata.

**The endpoint is generic.** No code in `bot.py` or in Cerid special-cases
the `"telegram-bot"` source type — the `field_mappings` config is the only
service-specific knowledge needed.

---

## Adapting for other services

Replace the `FIELD_MAPPINGS` dict and `_make_payload` function to match
the JSON shape your service produces, then post to the same endpoint.
See `docs/INTEGRATION_GUIDE.md` in the Cerid repo for worked examples
(Readwise, Pocket, Instapaper, Raindrop).
