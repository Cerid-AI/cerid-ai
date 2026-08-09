# Background Jobs — Operator Guide

> Audience: operator running a Cerid instance.
> Companion: [`docs/OPERATIONS.md`](OPERATIONS.md).
> Status: **operator guide (processor + scheduled-job overview).**

## 1. What the processor does

The Background Processor is one queue, one worker, one throttle for
**enqueued** work — parsing new documents, extracting entities, refreshing
community summaries, generating wiki pages, consolidating memory, composing
the daily brief, and similar jobs that use the Redis-backed processor queue.

**APScheduler** (in `app/scheduler.py`) is a **separate** plane: crons and
interval jobs (folder scan, webhook drain, model auto-update, graph nightly
jobs, etc.). Pausing or disabling the processor **does not** stop in-process
scheduler runners.

The processor lives inside the existing `app/main.py` container. It
does not require a separate service or deployment. It uses Redis (already
in the stack) for queue persistence.

**You will rarely interact with it directly.** The Monitoring pane in
the UI shows it; the `/health.invariants` endpoint summarizes it; the
Processor → Settings tab controls it. This document is for the moments
when something looks off.

## 2. Modes

| Mode | What runs | When to use |
|---|---|---|
| **Local-only** *(default)* | Every job against local Ollama. Zero API spend. | Privacy-first install. CPU-bound but free. |
| **Hybrid** | Cheap jobs local (embedding, basic entity extraction). Expensive jobs (community summaries, wiki generation, brief composition) via API. | When local Ollama can't keep up and you're willing to pay for speed. |
| **Disabled** | Queue accumulates; nothing executes. | Maintenance windows, controlled-environment installs. |

Switch in: Monitoring → Processor → Settings.

**Pausing is non-destructive for the processor queue.** Pending processor
jobs stay queued. Resume picks up where it left off. Disabling pauses
dequeue; it does not delete. **It does not pause APScheduler** — in-process
crons (rectify, polls, webhook drain, …) keep firing unless you stop the
MCP process or empty their `SCHEDULE_*` crons.

**Mode-fallback safety:** in Hybrid mode, each job is evaluated as it
runs — while the month's recorded spend is under the cap, jobs whose token
estimate exceeds `PROCESSOR_API_THRESHOLD_TOKENS` use the API model; once
spend reaches the cap, they re-route to local or hold, per the
`PROCESSOR_API_CAP_FALLBACK` policy (`local` | `hold`). The Monitoring
pane shows the active mode and the current month's spend against the cap.

## 3. Throttling

Workers pause dequeue when 1-minute load average exceeds the configured
ceiling. The default ceiling is `num_cpus × 0.7`.

```
WORKER_LOAD_CEILING=auto   # default — computes from /proc/cpuinfo
WORKER_LOAD_CEILING=3.5    # override; absolute loadavg
WORKER_CONCURRENCY=2       # default; per-worker concurrency
```

When throttling fires, `processor_throttled_ticks` increments in
`/health.invariants`. A high value (> 100/hour sustained) signals the
host is undersized for current queue depth. **This is operator signal,
not an error.** Options:

- Accept the slower drain rate (the queue catches up overnight).
- Increase `WORKER_CONCURRENCY` if memory allows.
- Move to Hybrid mode and let API absorb the expensive jobs.
- Upgrade host.

## 4. Cost

In Hybrid mode, the processor estimates each job's cost from its token
count and the chosen model, and records the actual cost after the job runs.

- **Pricing table:** `src/mcp/core/processor/cost.py` (`PricingTable`).
  Versioned; sourced from each provider's posted rates. Model ids match
  with or without the `openrouter/` prefix, and `:free` models price at zero.
- **Per-job estimate:** stamped on the job record at enqueue
  (`estimated_tokens_in/out`, `model`) via `BaseJob.new_record`.
- **Rolling tracking:** `processor_cost_usd_7d` (7-day) and the current
  month's spend, both on `GET /processor/status` and shown in the
  Monitoring → Processor spend meter.
- **Hard cap:** `PROCESSOR_MONTHLY_CAP_USD`, set in Settings or via
  `PATCH /settings`. When the month's recorded spend reaches the cap, API
  routing stops and jobs fall back per `PROCESSOR_API_CAP_FALLBACK`; the
  spend meter turns red as the cap is approached.

> Roadmap: a per-job pre-enqueue cost projection in the activity card and
> a projected-vs-actual 7d/30d chart are planned Monitoring refinements.

API keys are user-supplied. **Cerid never proxies through its own
account.** Keyless APIs (Wikipedia, Wikidata, arXiv, OpenStreetMap)
work out-of-the-box.

## 5. Pausing safely

Pause is the right move when you want to:

- Ingest a large pack without waiting for entity extraction to start.
- Reduce noise during a debugging session.
- Stop a runaway cost burn (use cost cap, but pause is the manual switch).

What happens when you pause:

- In-flight jobs complete cleanly.
- Pending jobs hold in queue. None are lost.
- Scheduled jobs (briefs, synthesis) skip if their window passes; status
  reflects "snoozed."
- `/health.invariants.processor_jobs_completed_24h` may show 0; this is
  expected.

When you resume, the worker picks up the next dequeue tick within 1 s.

## 6. Reading `/health.invariants`

Three processor-specific fields:

| Field | Meaning | Investigate when |
|---|---|---|
| `processor_jobs_completed_24h` | Rolling 24 h completion count. | Drops to 0 while mode = local-only or hybrid (and you haven't paused). Suggests worker is wedged. |
| `processor_cost_usd_7d` | Rolling 7-day actual API spend. | Approaching monthly cap; or rising unexpectedly. |
| `processor_throttled_ticks` | Last-hour count of dequeue cycles paused by load ceiling. | Sustained > 100/hour. Either host is undersized or `WORKER_LOAD_CEILING` is too aggressive. |

The existing `swallowed_errors_last_hour` field will also show processor
exceptions (every broad-catch in the processor calls
`log_swallowed_error`).

## 7. Worst-day playbook

Order matters. Check in sequence; escalate when a step doesn't resolve.

### Worker stops draining queue

1. Check `processor_throttled_ticks` — if high, host is at capacity. Wait
   or lower load, or increase concurrency.
2. Check `processor_jobs_completed_24h` — if 0 and mode is not Disabled,
   worker is wedged.
3. Inspect Sentry for `processor.*` tagged errors in the last 30 min.
4. Restart the `app/main.py` container. Jobs in `running` state past
   their max duration auto-mark failed and retry per `retry_count`.

### Cost overrun

1. Check Monitoring → Processor → Settings → 7d chart.
2. If hard cap not breached but projection looks wrong, audit the
   pricing table — provider may have changed rates.
3. If hard cap was breached, mode auto-falls-back. Verify banner is
   visible.
4. To stop a burn immediately: switch mode to Disabled.

### Redis disconnect

1. `/health.invariants` reports degraded processor state.
2. Worker holds; no jobs lost.
3. Restart Redis; reconnection is automatic.
4. If Redis fails repeatedly, check `docker compose logs redis` and disk
   space on the Redis volume.

### Queue blockage

1. Single job stuck in `running` past max duration triggers auto-fail
   and retry.
2. If retry exhausts `retry_count`, job moves to `failed`. Visible in
   Recent tab.
3. Inspect failed job's stage + error in Sentry. Most failures are
   transient (API rate limit, network); a sustained pattern signals a
   bug to file.

## 8. Worker extraction (future)

If `processor_throttled_ticks` is consistently > 200/hour on a warm host,
or worker latency p95 > 30 s for high-priority jobs, or
`/health.invariants` request latency rises during heavy processing — the
worker should be extracted into its own container.

Today we run in-process. The `core/processor/`
boundary is designed for clean extraction when the day comes.

## 9. Environment reference

```
# Mode
PROCESSOR_MODE=local        # local | hybrid | disabled
PROCESSOR_API_THRESHOLD_TOKENS=4000   # Hybrid: route jobs above this to API
PROCESSOR_MONTHLY_CAP_USD=5           # Hybrid hard cap
PROCESSOR_API_CAP_FALLBACK=local      # local | hold

# Worker
WORKER_CONCURRENCY=2
WORKER_LOAD_CEILING=auto    # auto | <float>
WORKER_MAX_RUNTIME_SECONDS=600

# Redis (existing)
REDIS_URL=redis://redis:6379/0
PROCESSOR_REDIS_KEY_PREFIX=cerid:proc
```

## 10. Knowledge-graph nightly jobs (subset)

The full APScheduler inventory is ~30+ jobs.
This section documents only the **$0 graph layout/trust** trio behind Subjects
panes. Empty `SCHEDULE_*` disables these three; `max_instances=1`. They run
back-to-back in the early-morning window so Atlas/Constellation/Timeline wake
to fresh data.

| Job id | Default cron | Trigger | Writes / produces |
|---|---|---|---|
| `compute_trust_state` | `31 3 * * *` | nightly | `Entity.trust_state ∈ {verified, partial, unverified}` from VerificationReport tallies. Thresholds: verified-share ≥ 0.70 → `verified`, ≥ 0.20 → `partial`, else `unverified` (override via `TRUST_STATE_VERIFIED_THRESHOLD` / `TRUST_STATE_PARTIAL_THRESHOLD`). Only entities with covering reports are written. Powers the Atlas/Constellation Trust lens. |
| `derive_domains` | `32 3 * * *` | nightly **+** `entities_added` event subscriber (debounced) | `Entity.primary_domain`, `Entity.domain_mix` (JSON, sorted desc by count then name), `Entity.primary_subcategory`, `Entity.domains_updated_at`. 4-rung tie-break (count → non-`general` → recency → lexicographic). Orphan entities (no MENTIONS path) get the three domain fields `REMOVE`d. Backs `GET /graph/domains` and the family-wide Domain lens. Busts the `cerid:graph:emb3d:*` cache on completion. |
| `compute_umap_3d` | (gated) nightly | nightly + manual | Constellation/Atlas positions, community hulls, and the decomposition tree. Emits **three layout passes** — `force` (default), `wells`, `domain` — feeding `GET /graph/map?layout=`. The `force` layout (2026-07-02 re-tune) fills a disc rather than a hollow ring: Vogel/sunflower disc-fill warm-start + **strong/harmonic gravity** (`UMAP_FORCE_GRAVITY=0.08`), plus a weaker **domain-centroid cohesion** (`UMAP_FORCE_DOMAIN_PULL=0.3`) under the community pull for macro-domain differentiation. Z-axis carries recency (`updated_at`); community `short_label` derives from `Community.summary` via a `_first_clause` regex that strips boilerplate lead-ins. Caches under `cerid:graph:emb3d:v5:*`. |

> **Operational gotcha (`compute_umap_3d`):** run it via the scheduler runner
> — `docker exec … python -c "import asyncio; from app.scheduler import _run_compute_umap_3d; asyncio.run(_run_compute_umap_3d())"`. Directly
> instantiating `ComputeUmap3DJob()` has **no Neo4j driver bound**, so it
> silently falls back to hub-name labels instead of summary-derived ones.

All three jobs are registered in `app/scheduler.py` (`_run_derive_domains`,
`_run_compute_trust_state`, `_run_compute_umap_3d`); the per-job cache-bust
map (`derive_domains` → `cerid:graph:emb3d:*`, etc.) also lives there. See
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md) § Domain backbone for the data model.

## 11. Status

Processor modes, cost caps, and the graph nightly trio are implemented.
The complete scheduler table is visible at runtime via `GET /scheduler`.
