# ADR — Background Processor: In-Process vs Extracted Worker

> **Status:** Stub. Recorded for future-proofing; not currently actioned.
> **Driver:** [`tasks/2026-05-10-v0.92-final-plan.md`](../tasks/2026-05-10-v0.92-final-plan.md) Phase P.
> **Companion:** [`docs/BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md).

## Context

The Background Processor (Phase P, v0.92) runs as a thread/asyncio task
inside the existing `app/main.py` container. This is the right choice for
v0.92:

- No new container in the operator's compose file.
- No new deployment surface to manage.
- Redis (already in stack) provides queue persistence + recovery.
- Worker is a small surface (one async task, one router, one set of
  concrete job classes); the cost of co-locating with the request path
  is low while load is modest.

The `core/processor/` package is **pure logic** (job ABC, queue protocol,
cost estimator, priority calculus, no app or framework imports). The
`app/processor/worker.py` is the only piece that runs as part of the
FastAPI process. This boundary is intentional — see "Lift procedure"
below.

## Decision

**Run in-process for v0.92.** Extract to a separate container when
trigger signals fire (see below). Do not extract preemptively.

## Trigger signals

Extract when **any** of these is sustained for one week or longer:

| Signal | Threshold | Meaning |
|---|---|---|
| `/health.invariants.processor_throttled_ticks` | > 200 per hour, sustained | Worker is starving the request path |
| Worker job latency p95 | > 30 s for high-priority jobs | Worker can't keep up with interactive load |
| `/health.invariants` HTTP latency p95 | rises > 50 % during peak processing windows | Worker is competing for the event loop |
| Operator pain | repeated reports that pauses are needed for normal request-serving | Co-location is a UX problem |

If none of these are true at six-month review, extraction stays
deferred and this ADR is re-stamped.

## Lift procedure (when triggered)

The boundary makes the lift mechanical, not architectural.

### Step 1 — Container split
- Add a new compose service `cerid-worker` from the same image.
- Worker container runs `python -m app.processor.worker` instead of
  `uvicorn app.main:app`. Reads the same env, connects to the same Redis.

### Step 2 — Disable in-process worker
- Behind an env flag `PROCESSOR_INPROCESS_WORKER=0`, `app/main.py`
  skips starting the worker task during lifespan setup.
- Default flips per environment after one week of green chaos-suite
  runs against the split topology.

### Step 3 — Observability split
- `cerid-worker` reports to the same Sentry project with `service=worker`.
- Health endpoint `/health/worker` (read from Redis state) joins
  `/health.invariants` for the API container.

### Step 4 — Scale independence
- Worker concurrency configured per-container; API and worker scale
  separately.
- Hybrid-mode cost cap still global (Redis-tracked counter, not per-process).

### Step 5 — Document operationally
- Update `docs/BACKGROUND_JOBS.md` § 9 (env) and § 7 (worst-day playbook)
  to reflect two-container topology.
- Update `docker-compose.yml` and `docker-compose.ci.yml`.
- Preservation gate `I18` becomes two-container chaos check.

### What does NOT change
- `core/processor/` is untouched.
- Job classes in `app/processor/jobs/` are untouched.
- Public API surface is unchanged.
- No data migration.

## Cost of the lift (estimated)

- One operator PR: compose changes + env flag + smoke test. ~1 day.
- One preservation update: split-topology chaos scenario. ~1 day.
- One observability PR: per-service Sentry tags + worker health endpoint.
  ~1 day.

Total: ~3 person-days from trigger to merged. The cost of writing this
ADR stub now is ~1 hour and saves that week-of-scrambling when the
extraction need is real.

## Why we record this now

We will not remember the trigger signals in six months. The boundary
that makes the lift cheap exists today; documenting it locks in the
intent. If a future change degrades the boundary (e.g., adds a hidden
`app/processor → app/main` coupling), this ADR is the receipt that the
boundary mattered.

## Review cadence

Re-stamp this ADR at every release tag (v0.93, v0.94, …):

- Are the trigger signals true? If yes, plan the lift.
- Has the boundary degraded? If yes, file as tech debt.
- Has the rationale changed? Update.

## See also

- [`docs/BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md) — operator guide
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — layered system architecture
- `src/mcp/core/processor/` — the pure-logic boundary
- `src/mcp/app/processor/worker.py` — the extraction target
