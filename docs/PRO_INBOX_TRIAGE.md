# Pro — AI Inbox Triage

Categorize your Gmail + Outlook inboxes with one LLM pass per thread.
Cerid runs every 15 minutes when enabled, writes each triaged thread
back into the KB, and surfaces categories in chat.

## What this does

For each recent unread thread in Gmail and Outlook, Cerid produces:

| Field | Meaning |
|---|---|
| `category` | One of `urgent`, `actionable`, `personal`, `newsletter`, `promo` |
| `summary` | One-sentence paraphrase |
| `suggested_action` | "reply by EOD", "archive", "no action needed", etc. |

The categorization runs in the LLM stage `inbox_triage`. You can
route it to a different provider via `PROVIDER_STAGE_INBOX_TRIAGE=...`
(e.g. local Ollama for privacy, OpenRouter for accuracy).

The categorization rules favor conservative triage: only urgent
threads with clear same-day deadlines get marked `urgent`. Default
fallback is `actionable` so the user sees a thread rather than
having it buried under `promo`.

## How to enable

Two gates — both must be open:

1. **Pro feature flag**: `inbox_triage` is on by default for Pro
   accounts. For self-hosted: set `CERID_TIER=pro` in `.env`.
2. **Operator opt-in**: set `CERID_INBOX_TRIAGE_ENABLED=true` in
   `.env` then restart the MCP container. (This double-gate prevents
   inadvertent LLM cost on every Pro install.)

Prerequisites:

  - Gmail connector configured (`docs/PRO_GMAIL.md`)
  - Outlook connector configured (`docs/PRO_OUTLOOK.md`), or
  - At least one of the two — Cerid runs whichever is configured

## Cadence

Default: **every 15 minutes** while the toggle is on.

Override via the cron expression env var:

```bash
SCHEDULE_INBOX_TRIAGE="0 */2 * * *"   # every 2 hours
SCHEDULE_INBOX_TRIAGE=""              # disable the cron entirely
```

Each run has these cost guards:

  - `INBOX_TRIAGE_MAX_PER_SOURCE=30` — cap on messages fetched per
    source per run
  - `max_instances=1` on the scheduler job so overlapping runs can't
    pile up if the LLM is slow

## What gets ingested

Each triaged thread becomes one KB artifact in domain `inbox`. The
artifact's metadata carries the categorization payload so retrieval
can filter on it directly:

```json
{
  "source": "inbox_triage",
  "origin_source": "gmail",
  "category": "urgent",
  "summary": "Server outage requires same-day response",
  "suggested_action": "page on-call rotation",
  "thread_id": "outage discussion",
  "subject": "URGENT: prod outage",
  "latest_at": "2026-05-22T14:30:00Z",
  "message_count": "4"
}
```

Idempotency: re-triaging the same thread updates the same artifact
(via `source_id = "inbox_triage:<source>:<thread_id>"`). Chroma's
content_hash dedup keeps the no-op case cheap.

## Querying from chat

Two MCP tools surface the results:

**`pkb_inbox_triage`** — runs a fresh triage pass (LLM-per-thread,
cost_class=high).

**`pkb_inbox_filter`** — read-only query against already-triaged
threads (no LLM, cost_class=low). This is what natural-language chat
questions hit:

  - "what's urgent today?" → `pkb_inbox_filter(category="urgent")`
  - "newsletters from this week" → `pkb_inbox_filter(category="newsletter", since_days=7)`
  - "actionable Outlook threads" → `pkb_inbox_filter(category="actionable", source="outlook")`

Citations in the chat response point back to the KB artifact
(`artifact_id`), which retains the link to the original Gmail /
Outlook message via the source's web_url metadata.

## Privacy posture

  - All processing local — the LLM call route is controlled by
    `PROVIDER_STAGE_INBOX_TRIAGE`. Default uses your global
    `INTERNAL_LLM_PROVIDER` setting.
  - The triage prompt sees an excerpt of each thread (~2KB cap) for
    categorization. No bulk email content is sent in a single call.
  - Privacy filters still apply — if a domain is privacy-gated, it
    can't surface in retrieval at lower private_mode levels.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Scheduler logs "0 threads" repeatedly | No Gmail/Outlook unread in the last 24h | Expected — set a wider `query` like "newer_than:7d" via `pkb_inbox_triage` manual call |
| `feature_gated` in skipped list | Pro flag off | Set `CERID_TIER=pro` + restart |
| `not_configured` in skipped list | OAuth not completed for that source | See `docs/PRO_GMAIL.md` / `docs/PRO_OUTLOOK.md` |
| Categorizations seem off | LLM stage drifting | Try `PROVIDER_STAGE_INBOX_TRIAGE=openrouter` for stronger model, or write a custom prompt override via `INBOX_TRIAGE_PROMPT` env (future work) |
| Cron not firing | `CERID_INBOX_TRIAGE_ENABLED` unset, or empty `SCHEDULE_INBOX_TRIAGE` | Verify both, restart MCP container |
| Duplicate threads in KB | Source thread_id grouping misses (Gmail returned messages without subject prefix normalization caught) | Open an issue with the source's raw payload |

## Future work

- **Per-conversation thread_id** — current grouping uses subject
  normalization (drops Re:/Fwd:). The MCP tool surfaces don't expose
  Gmail's `threadId` / Outlook's `conversationId` yet. Wiring these
  through the DataSource layer is tracked for Phase J.2.
- **Custom prompt override** — let operators tune the triage prompt
  for their domain (e.g. legal review categories). Currently the
  prompt is fixed.
- **Suggested-reply drafts** — `suggested_action` is a phrase; a
  future enhancement could expand the "reply by EOD" cases into
  full draft responses on demand.
