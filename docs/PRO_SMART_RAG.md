# Pro — Custom Smart RAG

Tune which sources influence Cerid's retrieval rankings. Boost what
matters; demote what doesn't.

## What this does

Every result Cerid returns has a relevance score blended from
multiple sources — your KB collections, external data sources
(Gmail, Calendar, Outlook, Apple connectors), and web search.
Custom Smart RAG lets you tune each of those by setting a per-source
weight between 0.0 (silence the source) and 2.0 (boost it heavily).
The default is 1.0 = no change.

The weights compose multiplicatively. A Gmail result that's also
tagged with KB domain `mail` would receive both the `gmail` weight
and the `kb:mail` weight applied in series.

## Where to configure

**Settings → Pipeline → Smart RAG Config**

You'll see one row per source — a slider, the current weight, and a
short description. Drag to adjust; click "Save" to persist. The
"Reset all" button clears every override back to 1.0.

## Source naming convention

| Source kind | Name pattern | Examples |
|---|---|---|
| Data source (external) | `<name>` | `gmail`, `google_calendar`, `wikipedia`, `apple_calendar` |
| KB collection (internal) | `kb:<domain>` | `kb:notes`, `kb:mail`, `kb:meetings`, `kb:personal` |

The "Sources" list updates dynamically as you register new
connectors. If you've enabled Gmail or Apple Mail recently, refresh
the settings page to see them appear.

## How weights affect retrieval

At query time, every result's relevance score is multiplied by the
applicable weight(s) BEFORE Maximal Marginal Relevance
diversification. Practical consequences:

- **Setting Gmail to 1.5** doesn't make Gmail show up in queries
  where it has nothing to say — it just boosts Gmail-flavored
  answers when they're already candidates.
- **Setting `kb:meetings` to 2.0** makes meeting transcripts dominate
  retrieval when they're a possible answer source.
- **Setting Wikipedia to 0.0** effectively silences the source —
  results will still be fetched (free) but won't influence ranking.

The system clamps to `[0.0, 2.0]`. Out-of-range values are accepted
by the API but silently capped.

## Estimated recall impact

The Settings UI shows an "estimated recall impact" hint when you
have unsaved changes — a directional indicator (`+12%` / `-3%`) that
averages your deltas. **This is a heuristic, not a guarantee.** True
recall impact depends on your query distribution, KB composition,
and which sources are actually active. Treat the number as a
ballpark.

## Privacy posture

- Weights are stored in Redis (single-user: `cerid:rag:weights:global`;
  multi-user: `cerid:rag:weights:user:<user_id>`).
- No weights data leaves your Mac.
- The feature gate `custom_smart_rag` is Pro-tier. Free users see
  the panel with a lock overlay + upgrade CTA.
- Existing privacy filters (e.g. `messages` requires private_mode
  Level 2+) still apply — weights cannot un-hide privacy-gated
  domains.

## REST surface

For automation / scripts:

```bash
# Current weight map
curl http://localhost:8888/settings/rag/weights

# Set weights (Pro-tier only — returns 403 otherwise)
curl -X PUT http://localhost:8888/settings/rag/weights \
  -H "Content-Type: application/json" \
  -d '{"weights":{"gmail":1.5,"kb:notes":0.7}}'

# Reset to defaults
curl -X DELETE http://localhost:8888/settings/rag/weights

# Enumerate known sources for the UI
curl http://localhost:8888/settings/rag/weights/sources
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Sliders locked, "Pro feature" overlay | Community tier or feature flag off | Upgrade to Pro, or set `CERID_FEATURE_TIER=pro` in `.env` for self-hosted |
| Save returns 403 | `custom_smart_rag` feature off server-side | Verify `CERID_FEATURE_TIER=pro` then restart MCP container |
| Changes don't seem to affect rankings | Weights only matter when source is active | Confirm the source is enabled in Sources → Connectors, and your query actually touches that source |
| Sliders show non-default weights you didn't set | Stale state from prior session | Click "Reset all" to start fresh |
| No effect after save | Query cache holding old results | Issue a new query; the cache invalidates per-query, not per-weight-change |

## Notes for self-hosted operators

- Single-user mode (default): weights are global; no per-user storage.
- Multi-user mode: weights are scoped per user_id via the tenant
  context middleware. Different users see different weight panels
  with no cross-contamination.
- Telemetry: weight overrides log to `ai-companion.rag_weights`
  logger at INFO. No PII surfaces.

## Future work

- **Per-query weight overrides** — boost a source for one specific
  question without changing the global tuning. Tracked for a future
  sprint.
- **Recall@k validation** — compare retrieval quality before/after
  weight changes against a reference set. Currently the impact
  estimate is purely directional.
