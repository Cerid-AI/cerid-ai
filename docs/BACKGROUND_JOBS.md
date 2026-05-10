# Background Jobs — Operator Guide

> Audience: operator running a Cerid instance.
> Companion: [`docs/OPERATIONS.md`](OPERATIONS.md).
> Driver: [`tasks/2026-05-10-v0.92-final-plan.md`](../tasks/2026-05-10-v0.92-final-plan.md) Phase P.
> Status: **skeleton — fleshed out as P.1–P.3 land.**

## 1. What the processor does

The Background Processor is one queue, one worker, one throttle. Every
unit of background work in Cerid — parsing new documents, extracting
entities, refreshing community summaries, generating wiki pages,
consolidating memory, composing the daily brief, running the weekly
LongMemEval — runs through it.

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

**Pausing is non-destructive.** Pending jobs stay queued. Resume picks
up where it left off. Disabling pauses; it does not delete.

**Mode-fallback safety:** if Hybrid mode hits the monthly cost cap, the
processor auto-falls-back to local-only and surfaces a banner. In-flight
API jobs are allowed to complete; pending API jobs hold or re-route to
local based on `PROCESSOR_API_CAP_FALLBACK` policy.

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

In Hybrid mode, the processor projects per-job cost before enqueue and
tracks actual cost after.

- **Pricing table:** `src/mcp/core/processor/pricing.py`. Versioned;
  sourced from each provider's posted rates.
- **Pre-enqueue projection:** displayed in the activity card before any
  API-routed job runs.
- **Rolling tracking:** `processor_cost_usd_7d` in `/health.invariants`
  + 7d/30d projected-vs-actual chart in Monitoring → Processor → Settings.
- **Hard cap:** set in Settings. When monthly spend crosses the cap, mode
  auto-falls-back to local-only with a banner.

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

See [`docs/EXTRACTION_PLAN.md`](EXTRACTION_PLAN.md) for the trigger
criteria and lift procedure. Today we run in-process. The `core/processor/`
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

## 10. Status

**This document is a skeleton.** Sections expand as Phase P ships:

- Section 2 (Modes) — finalized when P.3 lands.
- Section 4 (Cost) — pricing table reference + actual-tracking details
  expand with P.3.
- Section 7 (Worst-day playbook) — fleshed out from chaos-suite findings
  in P.4.
- Section 9 (Env reference) — final list audited against
  `scripts/gen_env_example.py` at P.3 close.
