# Enterprise — Audit log

An append-only, hash-chained record of administrative and security actions.
Reading it back is the Enterprise `audit_logging` feature; the recording is
unconditional.

Not to be confused with `/agent/audit`, which audits knowledge *quality*
(hallucination and contradiction checks). This is the security log.

## What it guarantees, and what it does not

**It guarantees that selective tampering is detectable.** Every record carries
the SHA-256 of the record before it, and a sidecar records where the chain
currently ends. Change a field, delete a line, insert one, reorder two, or lop
records off the end, and `GET /audit-log/verify` reports the sequence number
where it happened.

**It does not make the log unalterable.** Anyone who can write to
`$DATA_DIR/audit/` can rewrite the whole chain and the head marker together.
Chaining raises the cost of hiding one action from "delete a line" to "rewrite
everything after it and keep the two files agreeing" — which is a real and
useful bar, and is the honest description of it. If you need more, ship the
segments to append-only storage you control and verify them there; the format
is line-delimited JSON precisely so that is easy.

**Reads are not logged.** Every query against a personal knowledge base is not
an audit trail, it is a surveillance record, and the storage cost is unbounded.
Administrative and security actions are.

## What is recorded

| Action | Where it fires |
|---|---|
| `license.activate` | `/license/activate` — success, and every rejection (`outcome: denied`) |
| `license.deactivate` | `/license/deactivate` |
| `artifact.delete` | `DELETE /admin/artifacts/{id}` |
| `kb.clear_domain` | `POST /admin/kb/clear-domain/{domain}` |
| `plugin.enable` | `POST /plugins/{name}/enable` |
| `plugin.disable` | `POST /plugins/{name}/disable` |

A rejected license key records the reason and **never the key**. The log is
readable by anyone entitled to read it, so it must not become a place secrets
accumulate.

Record shape:

```json
{
  "seq": 4,
  "ts": "2026-08-11T14:02:11.104233+00:00",
  "actor": "system",
  "action": "artifact.delete",
  "target": "8f3c…",
  "outcome": "success",
  "detail": {"chunks_removed": 12},
  "prev": "9ab1…",
  "hash": "4c77…"
}
```

## Reading it

Settings → System → Audit Log (RA-32) lists recent records with a verify-chain
status chip, filterable by outcome. It is the same two endpoints below, so
anything visible there is also fetchable directly:

```bash
K=$(grep -m1 '^CERID_API_KEY=' .env | cut -d= -f2-)

# Newest first, filterable by action prefix and outcome
curl -s -H "X-API-Key: $K" 'localhost:8888/audit-log?limit=50&action_prefix=license.' | jq

# Has anything been altered?
curl -s -H "X-API-Key: $K" localhost:8888/audit-log/verify | jq
```

`/audit-log/verify` returns **200 with `ok: false`** on a tampered log. That is
deliberate: a 5xx would make "the check could not run" and "the check failed"
the same HTTP status, which is the substitution this subsystem exists to avoid.

Both endpoints return 403 below Enterprise, and 503 if the feature gate itself
cannot be evaluated — a gate that cannot answer refuses rather than serving the
surface on the way past.

## Storage and retention

Segments live in `$DATA_DIR/audit/` as `audit-00000.jsonl`, `audit-00001.jsonl`,
… plus `audit-head.json`. A segment rolls at
`CERID_AUDIT_LOG_MAX_SEGMENT_BYTES` (default 32 MiB).

**Nothing is deleted automatically.** An audit trail that prunes itself is worth
less than none, because the absence of a record stops meaning anything. Removing
old segments is an operator decision, taken with a shell — and it will make
`verify` report a break, which is the correct outcome.

> `CERID_AUDIT_RETENTION_DAYS` used to appear in `.env.example` with a default
> of 365. It configured nothing: it was scaffolding for a Redis-stream design
> that was never built, and no code read it. Removed 2026-08-11.

## When writes fail

`audit()` does not raise. An unwritable log must not brick a self-hosted
install, so a dropped record is reported instead of thrown:

- `/health.audit_log.write_failures` counts them since process start
- each one also reaches `/health.swallowed_errors_last_hour`
- the failure is logged at ERROR with the action that was lost

That is fail-open-and-loud, chosen deliberately. Call sites that must not
proceed unaudited call `audit_log.record()` and handle `AuditLogError`
themselves; none currently do.

## History

Added 2026-08-11. An earlier description of this feature said "every
read/write/admin action, exported to compliance-grade storage"; neither the
read logging nor the export is part of it, and the description has been
corrected to match. The scope above is the scope.
