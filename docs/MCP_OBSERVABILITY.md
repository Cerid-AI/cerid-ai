# MCP observability contract

Every tool call surfaced by cerid-kb's MCP server emits three signals,
unified through `app/tools.py::execute_tool`'s instrumentation wrapper
(Phase 2.1). The wrapper covers all dispatchers (decorator-registered,
legacy `MCP_TOOLS` if/elif, trading-internal, external `ext_*`) so
adding a new tool gets observability for free.

## Audit log

Every dispatch emits one INFO line on the `ai-companion.mcp_tool_audit`
logger with structured `extra` fields:

```python
{
    "tool_name": "pkb_artifact_get",
    "args_summary": {"artifact_id": "abc-…"},   # PII-redacted, size-capped
    "duration_ms": 142.3,
    "outcome": "ok",            # 'ok' or 'error'
    "error_class": None,        # class name when outcome=='error'
}
```

`args_summary` redacts credential-like keys (`password`, `token`,
`secret`, `api_key`, `authorization`) and truncates strings over 256
chars to `<str[N]>`. Composite types over the size cap are summarised
as `<list[len=N]>` / `<dict[len=N]>`. The redaction is defensive — never
trust the LLM to pass clean args.

Greppable via:

```bash
docker logs ai-companion-mcp 2>&1 | jq 'select(.tool_name == "pkb_rate")'
```

(when the log shipper is configured for JSON; the stdlib formatter
emits structured `extra` fields when the formatter is configured for
them — see `app/main.py::configure_logging`.)

## Metrics

Two metric names, written via `utils.metrics.get_metrics_collector()`:

* `mcp_tool_call_duration_ms{tool, outcome}` — histogram of per-call
  latency.
* `mcp_tool_call{tool, outcome, error_class}` — counter of dispatches.

Both fire-and-forget — metric-write failures never propagate to the
tool caller. Tags allow per-tool / per-outcome rollups in the
`/observability/metrics` aggregator.

### Rollups surfaced under `/health.invariants.mcp`

```json
{
  "mcp": {
    "window_minutes": 60,
    "calls": {"ok": 1234, "error": 5, "total": 1239, "error_rate": 0.004},
    "latency_ms": {"p50": 110.0, "p95": 420.0, "p99": 800.0, "avg": 145.2, "max": 2103.0, "count": 1239},
    "top_tools_by_error": [
      {"tool": "pkb_artifact_get", "error_class": "ResourceNotFoundError", "count": 3},
      {"tool": "pkb_web_search",   "error_class": "UpstreamUnavailableError", "count": 2}
    ]
  }
}
```

`/health` is a fast probe (Redis-backed counter); the heavier
`/observability/metrics/{name}` surface returns the raw time series
for charting.

## Sentry binding

`sentry_sdk.set_tag("mcp_tool", <name>)` is set on the current scope
before dispatch so any exception that bubbles up gets binned per-tool
in the Sentry dashboard. Failing tools are visible to operators
without grep-spelunking the audit log.

## Adding a new tool

`@register_tool` handles all three signals automatically. Tool authors
**should not** add their own metric writes — duplication produces
inconsistent per-tool aggregates. Use the existing channels and let
the wrapper bind them.

## Latency budgets (Phase 2 informal contract)

| `cost_class` | Target p95 |
|---|---|
| `low` | ≤ 200 ms |
| `medium` | ≤ 2 s |
| `high` | ≤ 8 s |

Tools that consistently exceed their budget should either:

* Optimise (cache, batch, async-ify the slow path)
* Be reclassified upward in the next release
* Surface degradation through `_warnings` (see [`MCP_TOOL_STYLE.md`])

A future contract test (planned v0.96+) will fail-fast when a tool's
observed p95 over 100 calls exceeds the budget by 50%, so reclassification
doesn't silently rot.
