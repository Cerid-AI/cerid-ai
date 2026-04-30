# Observability

Cerid AI ships three independent observability layers, each opt-in:

| Layer | Default | Where data goes | Use for |
|---|---|---|---|
| **structlog + Sentry** | always on (Sentry needs DSN) | Sentry org `cerid-ai` | Errors, breadcrumbs, swallowed-exception telemetry |
| **Step timing** | on (Workstream E Phase 0 default) | Response payload + Sentry trace | Per-stage latency in the 22-step query pipeline |
| **Langfuse** | off (requires env keys) | Local Postgres, on-box only | LLM trace tree, sampled LLM-judge faithfulness scoring |

The three layers compose: a single `/agent/query` request becomes a Sentry trace (HTTP-shape spans, errors), a structured-log timeline tagged with `stage="<step>"`, and a Langfuse trace (LLM-shape spans with prompts, responses, scores).

## Step timing

Workstream E Phase 0 flipped `ENABLE_STEP_TIMING` to default `true`. Every `/agent/query` response now carries a `timings` field:

```json
{
  "answer": "…",
  "timings": {
    "query_enrich": 12,
    "multi_domain_query": 234,
    "rerank": 89,
    "context_assemble": 45
  }
}
```

Per-request override: pass `X-Debug-Timing: false` to suppress (e.g. for benchmark runs that want the smaller payload). Pass `true` explicitly when the env default is off.

The timing data also lands in structlog as `stage="<step>"` events — this is the canonical log breadcrumb for cost telemetry and the field that Langfuse spans tag with for cross-tool joins.

## Langfuse self-hosted

Langfuse provides an LLM-aware trace tree (vs Sentry's HTTP-aware tree) plus dashboards for LLM cost / latency / quality and a sampled LLM-judge that scores response faithfulness on production traffic.

**The Cerid deployment is loopback-only by default. Nothing leaves the box unless an operator explicitly sets `LANGFUSE_HOST` to a remote URL.**

### Bring-up

1. Generate the four secret-grade env vars (Langfuse refuses to boot without them):

   ```bash
   echo "LANGFUSE_DB_PASSWORD=$(openssl rand -base64 24)" >> .env
   echo "LANGFUSE_AUTH_SECRET=$(openssl rand -base64 32)" >> .env
   echo "LANGFUSE_SALT=$(openssl rand -base64 24)" >> .env
   echo "LANGFUSE_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env
   ```

2. Bring up the stack:

   ```bash
   docker compose -f stacks/langfuse/docker-compose.yml up -d
   ```

3. Open `http://localhost:3000` (loopback only). Create your org + project. Copy the public + secret keys.

4. Wire the MCP container:

   ```bash
   echo "LANGFUSE_PUBLIC_KEY=pk-..." >> .env
   echo "LANGFUSE_SECRET_KEY=sk-..." >> .env
   # LANGFUSE_HOST defaults to http://langfuse:3000 (correct for the docker network)
   docker compose restart mcp-server
   ```

5. Issue a test query. Confirm the trace appears in the Langfuse UI within ~10s.

### Privacy contract

What stays on the box (default config):

- Langfuse Postgres holds: trace metadata, prompts, completions, scores. **All of this is your data.**
- The Langfuse container's `TELEMETRY_ENABLED=false` suppresses the upstream pingback that the Langfuse cloud version uses for product analytics.
- Port 3000 binds to `127.0.0.1` only — the UI is unreachable from other machines on the LAN until you change the bind.
- `AUTH_DISABLE_SIGNUP=true` prevents drive-by accounts on a misconfigured exposure.

What changes when you flip the switches:

- Setting `LANGFUSE_HOST=https://cloud.langfuse.com` (or any other remote) starts shipping every trace payload upstream. **Don't do this without an explicit privacy review.**
- Binding the host port to `0.0.0.0:3000` exposes the UI to your LAN. Pair with a reverse proxy that enforces auth.

### Verify "nothing leaves the box"

After bring-up, confirm with `tcpdump`:

```bash
# On the host, watch outbound traffic from the langfuse container
docker compose -f stacks/langfuse/docker-compose.yml exec langfuse \
  apk add --no-cache tcpdump 2>/dev/null
docker compose -f stacks/langfuse/docker-compose.yml exec langfuse \
  tcpdump -i eth0 -n 'not (host 127.0.0.1 or src net 172.16.0.0/12)' &

# Now issue a query against MCP and observe the dump
curl -s -X POST http://localhost:8888/agent/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"hello","domains":["general"]}' >/dev/null
```

You should see zero output between query and trace appearing in the Langfuse UI.

### Sampled LLM-judge

When enabled (Workstream E Phase 5b wires this into `query_agent.py`), 1–2% of production queries are scored for faithfulness by a Haiku-class judge model. The score is attached to the Langfuse trace as a `judge_score` field and aggregated weekly in dashboards. Sample rate via `LANGFUSE_LLM_JUDGE_SAMPLE_RATE` (default 0.02). Set to `0.0` to disable.

The judge call costs real money. The default 2% rate over a typical workload yields tens of judge calls per day; raise carefully. Budget alerts fire via the existing Sentry webhook when monthly judge-call cost crosses thresholds.

### Tear-down

```bash
make langfuse-down       # stops services, keeps data volume
make langfuse-purge      # stops services AND drops the data volume
```

A purge is irreversible. Sentry / structlog history is unaffected (different stack).

## Sentry

Errors and HTTP-shape traces. Configured in `src/mcp/main.py` via the `SENTRY_DSN_MCP` env var. Org `cerid-ai`, project `cerid-ai-mcp`. See the project's CLAUDE.md for DSN secret names.

## Cross-layer joins

Every observability event in the codebase carries the same `stage="<name>"` breadcrumb (CLAUDE.md logging contract). Same name across:

- structlog `stage="memory_extract"` log line
- Sentry span `op="retrieval.memory" name="memory_extract"`
- Langfuse span `metadata.stage="memory_extract"`

To diagnose a slow query: pull the Sentry trace, jump to the slowest span, find the matching `stage` in Langfuse for the LLM detail, search structlog for the same `stage` for the surrounding events.

## See also

- Driver doc: `tasks/2026-04-28-workstream-e-rag-modernization.md` (Phase 5b)
- Sentry / structlog conventions: `CLAUDE.md` "Mechanical overrides" §1, §2
- Langfuse client: `src/mcp/core/observability/langfuse_client.py`
- Sentry span helpers: `src/mcp/core/observability/span_helpers.py`
