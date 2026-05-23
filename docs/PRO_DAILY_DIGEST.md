# Pro — Daily Digest

A scheduled LLM-summarized "what happened in the last 24 hours" view
of your KB. Surfaces in-app, persists as a KB artifact, and fires a
webhook event for any external integrations.

## What this does

Every morning (default 7 AM UTC), Cerid:

1. Pulls every artifact ingested in the last 24 hours
2. Pulls curator-flagged content (artifacts with `quality_score`
   below a threshold)
3. Pulls urgent + actionable items from your Phase J inbox triage
4. Calls the LLM (stage `daily_digest`) to synthesize a structured
   digest with five sections
5. Persists the digest as a KB artifact in domain `digests`
6. Fires a `digest.ready` webhook event so the in-app notification
   surface (and any external integrations you've configured) can
   deliver it

The digest has five sections:

| Section | What's in it |
|---|---|
| `top_categories` | Ranked summary of which domains saw activity (notes, mail, meetings, …) |
| `key_threads` | Up to 5 standout artifacts the user should care about |
| `urgent` | Items needing same-day attention |
| `action_items` | Up to 10 concrete next-steps |
| `quality_alerts` | Curator findings (broken / incomplete artifacts) |

## How to enable

Two gates — both must be open:

1. **Pro feature flag**: `daily_digest` is on by default for Pro
   accounts. For self-hosted: set `CERID_FEATURE_TIER=pro` in `.env`.
2. **Operator opt-in**: set `CERID_DAILY_DIGEST_ENABLED=true` in
   `.env` then restart the MCP container.

## Cadence + budget

```bash
# Defaults
SCHEDULE_DAILY_DIGEST="0 7 * * *"   # daily at 7 AM server-UTC
DAILY_DIGEST_WINDOW_HOURS=24        # lookback window
```

Override the cron expression to change cadence:

```bash
SCHEDULE_DAILY_DIGEST="0 8 * * 1"   # Mondays at 8 AM
SCHEDULE_DAILY_DIGEST=""            # disable the cron entirely
```

`max_instances=1` on the scheduler job prevents overlapping runs if
the LLM is slow. The job's LLM cost per run is bounded — single
synthesis call (~1500 tokens output budget).

## REST surface

```bash
# Most recent digest summary (zero-cost — reads from KB)
curl http://localhost:8888/digests/latest

# Last N digests for the digest-strip UI
curl http://localhost:8888/digests/recent?limit=7

# Specific date (returns null when no digest exists for that day)
curl http://localhost:8888/digests/2026-05-22

# Trigger immediately (Pro-gated; honors feature flag but
# bypasses the env toggle since the user explicitly requested it)
curl -X POST http://localhost:8888/digests/run-now
```

## Subjects "Last 24h" filter

The digest's in-app notification deep-links into the Subjects pane
with `?since=<ISO timestamp>`. The pane shows a clearable filter
chip at the top — clicking the chip clears the filter.

For v1 the chip is a UI hint only; underlying graph queries don't
yet narrow on the timestamp. Wiring the filter through to
`/graph/neighborhood`'s response shape is tracked for Phase K.2.

## Webhook integration

When a digest completes, Cerid fires a `digest.ready` event to every
endpoint in `WEBHOOK_ENDPOINTS` that subscribes to it. Payload:

```json
{
  "digest_id": "uuid",
  "generated_at": "2026-05-22T07:00:00+00:00",
  "artifact_count": 12,
  "flagged_count": 2,
  "inbox_urgent_count": 1,
  "persisted_artifact_id": "art:abc123",
  "summary": "Your daily digest is ready."
}
```

The receiver fetches the full content via `GET /digests/latest` or
`GET /digests/{date}`.

Email delivery is **not** included in Phase K. SMTP infrastructure
requires operator credentials (SMTP server + creds, or transactional
email service API key) and is tracked as a separate work item.
Until then, deliver via the webhook to whatever notification surface
you've integrated (Slack, custom in-app, Apple Watch via Shortcuts).

## Privacy posture

- All processing local — the LLM call route is controlled by
  `PROVIDER_STAGE_DAILY_DIGEST=<provider>` (default: your global
  `INTERNAL_LLM_PROVIDER`).
- The digest sees compact one-line summaries of each artifact
  (≤200 chars each), capped at ~6KB total. No bulk content goes to
  the LLM.
- Existing privacy filters still apply — the digest **inherits** the
  global `private_mode` setting, so iMessage content (Level 2+
  required) is excluded from the digest when private mode is below
  the floor.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Scheduler logs "0 artifacts" | No KB ingest activity in window | Expected. The digest still persists as a "zero-activity" record. |
| `feature_gated` skip reason | Pro flag off | Set `CERID_FEATURE_TIER=pro` + restart |
| Cron doesn't fire | `CERID_DAILY_DIGEST_ENABLED` unset | Verify, then restart MCP container |
| Digest empty even with activity | LLM unavailable | Check `stage="daily_digest"` provider routing |
| 403 on `/digests/run-now` | Feature flag off | Same as feature_gated above |
| Quality alerts list always empty | No artifacts below `quality_threshold` | Default 0.5 — lower via `DAILY_DIGEST_QUALITY_THRESHOLD` env (e.g. 0.7 surfaces more) |

## Future work (Phase K.2)

- **Per-user timezone** — currently everyone gets server-UTC
  cadence. Multi-user mode will support per-user `digest_timezone`
  setting; the scheduler will compute N cron expressions or use
  per-user APScheduler jobs.
- **Email delivery worker** — when SMTP credentials are configured,
  a separate `_run_email_dispatch` job picks up `digest.ready`
  events and sends formatted email.
- **Subjects filter wiring** — make `?since=` actually narrow the
  Atlas / Constellation / Wiki queries (currently UI hint only).
- **Push notification template** — APNs / FCM integration for the
  mobile companion app.
