# Environment Variable Conventions

> **Last updated:** 2026-03-07
> **Reference:** `.env.example` (template), `src/mcp/config/settings.py` (reader)

## Naming Rules for New Variables

1. **App-level config** uses `CERID_` prefix: `CERID_STORAGE_MODE`, `CERID_SYNC_DIR`
2. **External service URLs** use the service name: `NEO4J_URI`, `REDIS_URL`, `CHROMA_URL`, `BIFROST_URL`
3. **Feature toggles** use `ENABLE_` prefix (no `CERID_`): `ENABLE_HALLUCINATION_CHECK`, `ENABLE_MODEL_ROUTER`
4. **Cron schedules** use `SCHEDULE_` prefix: `SCHEDULE_RECTIFY`, `SCHEDULE_SYNC_EXPORT`
5. **Tuning parameters** are bare descriptive names: `HYBRID_VECTOR_WEIGHT`, `HALLUCINATION_THRESHOLD`
6. **Secrets** are bare service names: `NEO4J_PASSWORD`, `OPENROUTER_API_KEY`, `CERID_API_KEY`
7. **Port overrides** use `CERID_PORT_` prefix: `CERID_PORT_GUI`, `CERID_PORT_MCP`

## Known Inconsistencies

These exist for historical reasons and should **not** be renamed (would break existing deployments):

| Variable | Pattern | Expected | Notes |
|----------|---------|----------|-------|
| `CATEGORIZE_MODE` | Bare | Should be `CERID_CATEGORIZE_MODE` | Predates `CERID_` convention |
| `CHUNKING_MODE` | Bare | Should be `CERID_CHUNKING_MODE` | Predates `CERID_` convention |
| `TOMBSTONE_TTL_DAYS` | Bare | Should be `CERID_TOMBSTONE_TTL_DAYS` | Added with sync feature |
| `ENABLE_*` flags | `ENABLE_` | Could be `CERID_ENABLE_*` | Matches common convention without prefix |
| `CERID_STORAGE_MODE` | `CERID_` | Consistent | Correct |
| `CERID_CONFLICT_STRATEGY` | `CERID_` | Consistent | Correct |

**Rule:** Do not rename existing variables. For new variables, follow the patterns above.

## Current Inventory

### Secrets (required)

| Variable | Source | Default | Description |
|----------|--------|---------|-------------|
| `NEO4J_PASSWORD` | `.env` | *(none)* | Neo4j database password |
| `OPENROUTER_API_KEY` | `.env` | *(none)* | OpenRouter API key for LLM access |
| `OPENAI_API_KEY` | `.env` | *(none)* | OpenAI API key (optional provider) |
| `CERID_USE_BIFROST` | `.env` | `false` | Enable Bifrost LLM gateway for intent routing. When `false` (default), LLM calls route directly to OpenRouter. |
| `INFERENCE_MODE` | `.env` | `auto` | Embedding provider: `auto` (detect best), `onnx-cpu`, `onnx-gpu`, `ollama`, `fastembed-sidecar` |
| `CERID_SIDECAR_PORT` | `.env` | `8889` | FastEmbed sidecar port for native GPU embeddings |

### Port Overrides (optional)

All services use sensible default ports. Override only when you have port conflicts or need custom port assignments (e.g., running multiple instances or behind a reverse proxy).

| Variable | Default | Description |
|----------|---------|-------------|
| `CERID_PORT_GUI` | `3000` | React GUI (host-side) |
| `CERID_PORT_MCP` | `8888` | MCP Server API (host-side) |
| `CERID_PORT_BIFROST` | `8080` | Bifrost LLM Gateway (host-side) |
| `CERID_PORT_NEO4J` | `7474` | Neo4j HTTP browser (host-side, bound to 127.0.0.1) |
| `CERID_PORT_NEO4J_BOLT` | `7687` | Neo4j Bolt protocol (host-side, bound to 127.0.0.1) |
| `CERID_PORT_CHROMA` | `8001` | ChromaDB (host-side, bound to 127.0.0.1) |
| `CERID_PORT_REDIS` | `6379` | Redis (host-side, bound to 127.0.0.1) |
| `CERID_PORT_TAILNET` | `8443` | Tailnet front-door hop (host-local HTTP; TLS terminated by `tailscale serve`) |

Port overrides affect the host-side port mapping only. Container-internal ports remain unchanged. The `start-cerid.sh` script exports these with defaults and uses them in preflight checks, health checks, and access URL output.

### Network & Access (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CERID_HOST` | *(auto-detected)* | LAN IP or hostname |
| `CERID_GATEWAY` | `false` | Enable Caddy HTTPS reverse proxy |
| `CERID_TAILSCALE` | `false` | Front the suite over the tailnet via host `tailscale serve` (dedicated HTTPS port, identity-trusted, serve-only). See LAN_REMOTE_ACCESS.md |
| `CLOUDFLARE_TUNNEL_TOKEN` | *(empty)* | Enable Cloudflare Tunnel for demos |

### MCP Application Config (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CATEGORIZE_MODE` | `smart` | Categorization tier: manual, smart, pro |
| `BIFROST_URL` | `http://bifrost:8080/v1` | LLM gateway URL |
| `BIFROST_TIMEOUT` | `30.0` | LLM gateway timeout (seconds) |
| `CERID_API_KEY` | *(empty)* | API key for MCP auth (opt-in) |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `WATCH_FOLDER` | `~/cerid-archive` | Host-side file watcher path |
| `DATA_DIR` | `data` | Data directory for BM25/tombstones |
| `ARCHIVE_PATH` | `/archive` | Container-side archive mount |

### Feature Toggles (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_FEEDBACK_LOOP` | `false` | Conversation feedback loop |
| `ENABLE_HALLUCINATION_CHECK` | `true` | Hallucination detection |
| `ENABLE_MEMORY_EXTRACTION` | `true` | Memory extraction from conversations |
| `ENABLE_MODEL_ROUTER` | `false` | Automatic model routing |
| `ENABLE_ENCRYPTION` | `false` | Field-level Fernet encryption |
| `ENABLE_EXTERNAL_VERIFICATION` | `true` | Cross-model claim verification |
| `ENABLE_AUTO_INJECT` | `false` | Auto-inject high-confidence KB results |

### Retrieval Tuning (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNKING_MODE` | `semantic` | Chunking strategy: semantic or token |
| `HYBRID_VECTOR_WEIGHT` | `0.6` | Vector search weight in hybrid retrieval |
| `HYBRID_KEYWORD_WEIGHT` | `0.4` | BM25 keyword weight in hybrid retrieval |
| `RERANK_LLM_WEIGHT` | `0.6` | LLM reranker weight |
| `RERANK_ORIGINAL_WEIGHT` | `0.4` | Original score weight after reranking |
| `EMBEDDING_MODEL` | `Snowflake/snowflake-arctic-embed-m-v1.5` | Sentence embedding model (768-dim) |
| `EMBEDDING_DIMENSIONS` | `768` | Embedding vector dimensions |
| `QUALITY_MIN_RELEVANCE_THRESHOLD` | `0.15` | Minimum relevance for results |
| `CONTEXT_BOOST_WEIGHT` | `0.08` | Conversation context alignment boost |
| `AUTO_INJECT_THRESHOLD` | `0.82` | Min relevance for auto-injection |

### Quenchforge / local GPU inference (optional)

Route LLM, embedding, and rerank calls to a local [Quenchforge](AMD_GPU_MODEL_RECOMMENDATIONS.md)
daemon (Ollama/OpenAI-compatible) instead of an external API. Each surface
opts in independently via its `*_PROVIDER` toggle.

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERNAL_LLM_PROVIDER` | `openrouter` | Set to `quenchforge` to route chat/LLM calls to the local daemon |
| `EMBEDDINGS_PROVIDER` | `sidecar` | Set to `quenchforge` to route embeddings to the local daemon |
| `RERANK_PROVIDER` | `sidecar` | Set to `quenchforge` to route reranking to the local daemon |
| `QUENCHFORGE_URL` | `http://host.docker.internal:11434` | Quenchforge daemon base URL (falls back to `OLLAMA_URL`) |
| `QUENCHFORGE_EMBED_MODEL` | _(empty — required when `EMBEDDINGS_PROVIDER=quenchforge`)_ | Embedding model name the daemon serves |
| `QUENCHFORGE_RERANK_MODEL` | `bge-reranker-v2-m3` | Rerank model name the daemon serves |

> ⚠ **`QUENCHFORGE_EMBED_MODEL` has no default on purpose.** It must match
> the model your corpus was embedded with. Matching `EMBEDDING_DIMENSIONS`
> (768) is necessary but **not sufficient** — `nomic-embed-text-v1.5` and
> `Snowflake/snowflake-arctic-embed-m-v1.5` are both 768-dim but occupy
> different vector spaces; swapping one for the other without re-embedding
> silently collapses retrieval quality. The client raises rather than guess.
> Rerank is a cross-encoder score (no stored vectors), so a default is safe.
> Full model matrix: [`AMD_GPU_MODEL_RECOMMENDATIONS.md`](AMD_GPU_MODEL_RECOMMENDATIONS.md).

### Verification (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `VERIFICATION_MODEL` | `openrouter/openai/gpt-4o-mini` | Primary verification model |
| `VERIFICATION_CURRENT_EVENT_MODEL` | `openrouter/x-ai/grok-4-fast:online` | Web search model for current events |
| `EXTERNAL_VERIFY_MODEL` | `openrouter/openai/gpt-4o-mini` | Cross-model verification model |
| `EXTERNAL_VERIFY_MAX_CONCURRENT` | `5` | Max concurrent external verifications |
| `HALLUCINATION_THRESHOLD` | `0.75` | Confidence threshold for hallucination flagging |
| `HALLUCINATION_MIN_RESPONSE_LENGTH` | `50` | Min response length for verification |
| `HALLUCINATION_MAX_CLAIMS` | `10` | Max claims extracted per response |

### Schedule Overrides (optional, cron expressions)

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULE_RECTIFY` | `0 3 * * *` | Rectification agent (daily 3 AM) |
| `SCHEDULE_HEALTH_CHECK` | `0 */6 * * *` | Health check (every 6 hours) |
| `SCHEDULE_STALE_DETECTION` | `0 4 * * 0` | Stale artifact detection (Sunday 4 AM) |
| `SCHEDULE_STALE_DAYS` | `90` | Days before artifact is considered stale |
| `SCHEDULE_SYNC_EXPORT` | *(empty)* | Sync export cron (empty = disabled) |
| `SCHEDULE_MODEL_AUTO_UPDATE` | `0 6 * * 1` | Auto-adopt latest in-family model per role (Mon 6 AM) |

### Database URLs (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://ai-companion-neo4j:7687` | Neo4j Bolt URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `REDIS_URL` | `redis://ai-companion-redis:6379` | Redis connection URL |
| `CHROMA_URL` | `http://ai-companion-chroma:8000` | ChromaDB server URL |

### Sync Config (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CERID_SYNC_DIR` | `~/Dropbox/cerid-sync` | Sync directory path |
| `CERID_MACHINE_ID` | *(hostname)* | Machine identifier for sync |
| `CERID_SYNC_BACKEND` | `local` | Sync backend type |
| `CERID_CONFLICT_STRATEGY` | `remote_wins` | Default conflict resolution |
| `CERID_STORAGE_MODE` | `extract_only` | Storage mode: extract_only or archive |
| `SYNC_EXPORT_ON_INGEST` | `false` | Auto-export after each ingest |
| `TOMBSTONE_TTL_DAYS` | `90` | Days before tombstones expire |

### Paths & Storage (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ARCHIVE_PATH` | `/archive` | Container-side archive mount |
| `WATCH_FOLDER` | `~/cerid-archive` | Host-side watcher path |
| `CERID_STORAGE_MODE` | `extract_only` | Storage mode |

### Plugin System (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CERID_TIER` | `community` | Feature tier: community or pro |
| `CERID_PLUGIN_DIR` | `plugins` | Plugin directory |
| `CERID_ENABLED_PLUGINS` | *(empty)* | Enabled plugin list |
| `CERID_CUSTOM_DOMAINS` | *(empty)* | JSON registering **built-in-like** custom domains (with descriptions/icons). Optional — external clients can ingest to and query **any** ad-hoc domain name without registering it here; this var is only for domains you want treated like built-ins. |

### Multi-User Auth (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CERID_MULTI_USER` | `false` | Enable multi-user JWT authentication |
| `CERID_JWT_SECRET` | *(none)* | JWT signing secret (required when multi-user enabled) |
| `CERID_JWT_ACCESS_TTL` | `1800` | Access token TTL in seconds (30 min) |
| `CERID_JWT_REFRESH_TTL` | `604800` | Refresh token TTL in seconds (7 days) |
| `CERID_DEFAULT_TENANT` | `default` | Default tenant ID for single-tenant mode |

### Encryption (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_ENCRYPTION` | `false` | Enable field-level encryption |
| `CERID_ENCRYPTION_KEY` | *(empty)* | Fernet encryption key |

### Memory (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_MEMORY_EXTRACTION` | `true` | Enable memory extraction |
| `MEMORY_RETENTION_DAYS` | `180` | Memory retention period |

### Cost Management (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_MODEL_ROUTER` | `false` | Automatic model routing |
| `MODEL_AUTO_UPDATE_ENABLED` | `true` | Auto-adopt the latest in-family model per role from the OpenRouter catalog (gates the `model_auto_update` scheduled job) |
| `COST_SENSITIVITY` | `medium` | Cost sensitivity level: low, medium, high |
