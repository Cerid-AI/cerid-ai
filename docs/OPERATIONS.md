# Cerid AI — Operations Guide

> **Created:** 2026-02-28 | **Updated:** 2026-03-21
> **Covers:** Startup, API key management, secrets rotation, rate limiting, branch protection, CI pipeline, Ollama, observability, plugins.

---

## Unified Docker Compose (Phase A)

As of Phase A, a single root `docker-compose.yml` replaces the previous 4-step startup sequence. All services (Infrastructure, Bifrost, MCP, React GUI) are defined in one file with `depends_on` healthchecks ensuring correct startup order.

> **Note:** Bifrost is now optional. Use `--profile bifrost` to enable it, controlled by the `CERID_USE_BIFROST` env var. When disabled, chat routes directly to OpenRouter.

```bash
# Start everything (preferred)
docker compose up -d

# Or use the startup script (adds LAN detection, validation, etc.)
./scripts/start-cerid.sh
```

The startup script still provides value for LAN IP detection, pre-flight validation, and post-startup reachability checks, but the underlying orchestration now uses the unified compose file.

---

## Local LLM via Ollama (Phase 48)

Ollama enables air-gapped deployment by routing pipeline intelligence tasks to a local LLM server.

### Setup

**Option A — GUI Setup Wizard (recommended):**
1. Navigate to Settings → System → Ollama → Set Up Ollama
2. Follow install instructions (macOS/Linux)
3. Click "Continue" — Cerid auto-detects Ollama and shows model selection
4. The wizard recommends a model based on your hardware (RAM/CPU/GPU):
   - **8GB+ RAM**: Llama 3.2 3B (2GB, lightweight)
   - **16GB+ RAM**: Llama 3.1 8B (4.7GB, balanced — recommended for most)
   - **32GB+ RAM**: Phi-4 14B (9.1GB, performance)
5. Select model → wizard pulls and enables automatically

**Option B — Manual:**
```bash
# Install Ollama (macOS)
brew install ollama && open -a Ollama

# Pull a model
ollama pull llama3.1:8b

# Enable in .env:
OLLAMA_ENABLED=true
OLLAMA_URL=http://host.docker.internal:11434  # Docker-to-host bridge
OLLAMA_DEFAULT_MODEL=llama3.1:8b
INTERNAL_LLM_PROVIDER=ollama
INTERNAL_LLM_MODEL=llama3.1:8b
```

### Post-Setup Model Management

Change the active model at any time from Settings → System → Ollama → Change button. The model management panel shows:
- All 3 catalog models with compatibility status based on your hardware
- Any additional models you've pulled manually
- One-click "Use" for installed models, "Install & Use" for new models

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ollama/chat` | POST | Streaming chat completion via local model |
| `/ollama/models` | GET | List available local models |
| `/ollama/pull` | POST | Pull a new model from Ollama registry (streaming NDJSON) |
| `/providers/ollama/status` | GET | Ollama status, models, reachability |
| `/providers/ollama/recommendations` | GET | Hardware-aware model recommendations |
| `/providers/ollama/enable` | POST | Enable Ollama (optional `{"model": "..."}` body) |
| `/providers/ollama/disable` | POST | Disable Ollama, fall back to cloud |

Ollama proxy includes a circuit breaker (5 failures → 60s open). When `OLLAMA_ENABLED=false` (default), proxy endpoints return 503.

### Verification Timeouts

Tuned for local inference speed (Ollama is slower than cloud APIs):
- `STREAMING_PER_CLAIM_TIMEOUT` — 10s per claim (default)
- Extraction timeout — 30s (internal, not env-configurable)
- `STREAMING_TOTAL_TIMEOUT` — 90s total verification deadline (default)

### Configuration

- `OLLAMA_ENABLED` — Enable/disable Ollama integration (default: `false`)
- `OLLAMA_URL` — Ollama server URL (default: `http://localhost:11434`; use `http://host.docker.internal:11434` for Docker)
- `OLLAMA_DEFAULT_MODEL` — Default model (auto-recommended if not set; fallback: `llama3.2:3b`)
- `INTERNAL_LLM_PROVIDER` — `ollama` or `bifrost` (default: `bifrost`)
- `INTERNAL_LLM_MODEL` — Model override (empty = use `OLLAMA_DEFAULT_MODEL`)

---

## Redis Configuration

Redis is configured with memory limits to prevent Docker OOM:

```
maxmemory 1gb
maxmemory-policy allkeys-lru
```

Container memory limit: 2GB (raised from 512MB in Phase 38). Socket timeout: 10s. Authentication via `REDIS_PASSWORD` env var.

---

## Observability Dashboard (Phase 47)

The observability system collects 8 Redis time-series metrics and exposes them via API.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/observability/metrics` | GET | Current metric values + sparkline data |
| `/observability/health-score` | GET | Weighted A-F health grade |

### Metrics Collected

Latency (P50/P95), LLM cost, NDCG retrieval quality, cache hit rate, verification accuracy, error rate, throughput, memory usage. All stored as Redis time-series with configurable retention.

---

## Plugin Management (Phase 49)

Plugins extend cerid-ai functionality without modifying core code.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/plugins` | GET | List all plugins with status |
| `/plugins/{id}/enable` | POST | Enable a plugin |
| `/plugins/{id}/disable` | POST | Disable a plugin |
| `/plugins/{id}/config` | GET/PUT | Read/update plugin configuration |
| `/plugins/scan` | POST | Scan for new plugins |

### Configuration

```bash
# In .env
CERID_TIER=community          # or "pro" for BSL-1.1 plugins
CERID_PLUGIN_DIR=plugins      # Plugin directory
CERID_ENABLED_PLUGINS=        # Comma-separated plugin IDs to auto-enable
```

---

## API Key Authentication

API key auth is **opt-in**. When `CERID_API_KEY` is set in `.env`, all non-exempt endpoints require the key via `X-API-Key` header. When unset, all requests pass through.

**Exempt paths:** `/health`, `/api/v1/health`, `/`, `/docs`, `/openapi.json`, `/redoc`, `/mcp/*`

### Key Rotation

1. Generate a new key: `openssl rand -hex 32`
2. Update `CERID_API_KEY` in `.env`
3. Re-encrypt: `./scripts/env-lock.sh`
4. Restart MCP container: `./scripts/start-cerid.sh --build`
5. Update all clients using the old key

**Security notes:**
- Uses HMAC constant-time comparison (prevents timing attacks)
- Auth failures log a SHA-256 hash prefix of the client IP (not raw IP)

**File:** `src/mcp/middleware/auth.py`

---

## Secrets Management

All secrets live in `.env` (git-ignored) and are encrypted as `.env.age` (committed) using [age](https://age-encryption.org/).

### Commands

| Action | Command |
|--------|---------|
| Decrypt | `./scripts/env-unlock.sh` |
| Encrypt | `./scripts/env-lock.sh` |
| Generate age key | `age-keygen -o ~/.config/cerid/age-key.txt` |

### Key Location

- **Private key:** `~/.config/cerid/age-key.txt` (or `$CERID_AGE_KEY` env var override)
- **Public key:** Hardcoded in `scripts/env-lock.sh`

### Rotation Policy

**Rotate immediately if:**
- A secret is accidentally committed or exposed
- A team member's access is revoked
- A third-party API key is compromised

**Rotate periodically (recommended quarterly):**
- `CERID_API_KEY` — regenerate with `openssl rand -hex 32`
- `CERID_ENCRYPTION_KEY` — regenerate Fernet key (note: existing encrypted fields must be re-encrypted with the old key first)
- `NEO4J_PASSWORD` — update in `.env` and Neo4j config, restart infrastructure stack
- `OPENROUTER_API_KEY` / `OPENAI_API_KEY` — rotate via provider dashboards

**After any rotation:**
1. Update `.env` with new values
2. Run `./scripts/env-lock.sh` to re-encrypt
3. Restart affected services: `./scripts/start-cerid.sh --build`
4. Commit `.env.age`: `git add .env.age && git commit -m "chore: rotate secrets"`

---

## Multi-User Authentication (Phase 33)

Multi-user JWT auth is **opt-in**. When enabled, all non-exempt endpoints require a Bearer token. Users register, login, and manage per-user API keys.

### Enable

```bash
# In .env
CERID_MULTI_USER=true
CERID_JWT_SECRET=$(openssl rand -hex 64)
```

Rebuild MCP after setting: `./scripts/start-cerid.sh --build`

### JWT Token Flow

1. **Register:** `POST /auth/register` → returns `access_token` + `refresh_token`
2. **Login:** `POST /auth/login` → returns `access_token` + `refresh_token`
3. **Access:** Include `Authorization: Bearer <access_token>` on all requests
4. **Refresh:** `POST /auth/refresh` with `refresh_token` → new `access_token`
5. **Logout:** `POST /auth/logout` → blacklists refresh token in Redis

### Token TTLs

| Token | Default | Env Var |
|-------|---------|---------|
| Access | 30 minutes | `CERID_JWT_ACCESS_TTL` |
| Refresh | 7 days | `CERID_JWT_REFRESH_TTL` |

### JWT Secret Rotation

1. Generate new secret: `openssl rand -hex 64`
2. Update `CERID_JWT_SECRET` in `.env`
3. Re-encrypt: `./scripts/env-lock.sh`
4. Restart MCP: `./scripts/start-cerid.sh --build`
5. **Note:** All existing tokens are immediately invalidated. Users must re-login.

### Per-User API Keys

Users can generate personal API keys via `PUT /auth/me/api-key`. Keys are encrypted with Fernet (`CERID_ENCRYPTION_KEY`) before storage in Neo4j. When multi-user is enabled, the `X-API-Key` header resolves to the owning user for rate limiting and audit logging.

### Usage Metering

Redis tracks per-user request counts with hourly buckets:
- Key pattern: `usage:{user_id}:{YYYY-MM-DD-HH}`
- Query via `GET /auth/me/usage` (returns last 24h + totals)
- TTL: 90 days auto-expiry

### Files

| File | Purpose |
|------|---------|
| `middleware/jwt_auth.py` | JWT creation/validation, Bearer extraction |
| `middleware/tenant_context.py` | ContextVar propagation (tenant_id, user_id) |
| `routers/auth.py` | 9 auth endpoints |
| `models/user.py` | User/Tenant Pydantic schemas |
| `db/neo4j/users.py` | User/Tenant Neo4j CRUD |
| `utils/usage.py` | Redis usage metering |

---

## Rate Limiting

In-memory sliding window rate limiter, per client IP.

### Limits

| Path | Max Requests | Window |
|------|-------------|--------|
| `/agent/*` | 20 | 60 seconds |
| `/ingest*` | 10 | 60 seconds |
| `/recategorize*` | 10 | 60 seconds |

### Response Headers (IETF standard)

All rate-limited responses include:
- `RateLimit-Limit` — max requests allowed
- `RateLimit-Remaining` — requests left in window
- `RateLimit-Reset` — seconds until window resets

429 responses also include `Retry-After`.

### Proxy Support

Set `TRUSTED_PROXIES` (comma-separated CIDRs) to extract real client IP from `X-Forwarded-For`. Without this, the direct peer IP is used.

### Known Limitations

- **In-memory only:** Rate limit state is lost on container restart. No warm-up period — limits reset to zero.
- **Single instance:** No distributed rate limiting. If running multiple MCP instances behind a load balancer, each tracks limits independently.
- **IP-based by default:** When `CERID_MULTI_USER=false`, all requests from the same IP share a single counter. When `CERID_MULTI_USER=true`, rate limits are keyed by authenticated user ID.

**File:** `src/mcp/middleware/rate_limit.py`

---

## Branch Protection

Branch protection is configured via **GitHub UI** (not checked into the repository).

### Recommended Settings for `main`

- **Require pull request reviews:** At least 1 approval
- **Require status checks to pass:** All 6 CI jobs (lint, test, security, lock-sync, frontend, docker)
- **Require branches to be up to date:** Enabled
- **Require linear history:** Recommended (prevents merge commits)
- **Do not allow bypassing:** Even for admins

### Required CI Checks

| Job | What It Checks |
|-----|----------------|
| `lint` | Ruff Python linting |
| `test` | 1376+ pytest tests, 70% coverage minimum |
| `security` | Bandit SAST + pip-audit dependency scan |
| `lock-sync` | Lock file freshness (pip-compile) |
| `frontend` | TypeScript types + ESLint + vitest + build + bundle size (<800KB) + npm audit |
| `docker` | Docker image build + Trivy CRITICAL/HIGH vulnerability scan |

---

## LAN Access (iPad / Other Devices)

Cerid AI can be accessed from any device on your local network (iPad, phone, second computer).

### Automatic Setup

The startup script auto-detects your LAN IP:

```bash
./scripts/start-cerid.sh
# Output: [net] CERID_HOST=192.168.1.42
# ...
# === LAN Access (iPad / other devices) ===
# React GUI: http://192.168.1.42:3000
# MCP API:   http://192.168.1.42:8888
```

### Manual Override

Set `CERID_HOST` in `.env` or export it before starting:

```bash
export CERID_HOST=192.168.1.42
./scripts/start-cerid.sh
```

### How It Works

1. `start-cerid.sh` detects the LAN IP via `ipconfig getifaddr en0` (macOS) or `ip addr` (Linux)
2. Exports `VITE_MCP_URL=http://<CERID_HOST>:8888`
3. The web container's `docker-entrypoint.sh` injects this URL into `/env-config.js` at runtime
4. The React GUI picks up `window.__ENV__.VITE_MCP_URL` for API calls
5. Bifrost chat API is proxied through nginx (`/api/bifrost/`), so no additional config needed

### CORS Configuration

MCP server defaults to `CORS_ORIGINS=*` (allows all origins). To restrict:

```bash
# In .env
CORS_ORIGINS=http://localhost:3000,http://192.168.1.42:3000
```

### Troubleshooting

- **iPad can't connect:** Ensure both devices are on the same WiFi network. Check macOS firewall allows ports 3000 and 8888.
- **MCP API errors on iPad:** Verify `VITE_MCP_URL` is set to the LAN IP (not `localhost`). Run `docker logs cerid-web` to check `/env-config.js` contents.
- **After IP change:** Re-run `./scripts/start-cerid.sh` — it re-detects the IP and regenerates the config.

---

## Caddy Reverse Proxy (Local HTTPS)

For HTTPS access on your local network (required by some iOS features):

```bash
# Set in .env
CERID_GATEWAY=true

# Start stack (Caddy starts as step [6/6])
./scripts/start-cerid.sh
```

Access via `https://<hostname>.local` (Bonjour/mDNS). Caddy uses `tls internal` for automatic self-signed certificates.

**Files:** `stacks/gateway/docker-compose.yml`, `stacks/gateway/Caddyfile`

---

## Cloudflare Tunnel (Public Demos)

For sharing Cerid AI publicly without port forwarding:

```bash
# Set in .env
CLOUDFLARE_TUNNEL_TOKEN=<your-tunnel-token>

# Start stack (tunnel starts as step [7/7])
./scripts/start-cerid.sh
```

Access via your Cloudflare tunnel hostname. Configure email-based OTP access policies in the Cloudflare Zero Trust dashboard for security.

**Files:** `stacks/tunnel/docker-compose.yml`

---

## CI Pipeline Reference

**Trigger:** Push to `main` + pull requests
**File:** `.github/workflows/ci.yml`

### Tool Versions (pinned in CI)

| Tool | Version | Used In |
|------|---------|---------|
| Ruff | 0.15.4 | `lint` job |
| Bandit | 1.9.4 | `security` job |
| pip-audit | 2.10.0 | `security` job |
| pip-tools (pip-compile) | 7.5.3 | `lock-sync` job |
| Python | 3.11 | All Python jobs |
| Node.js | 22 (from `.nvmrc`) | `frontend` job |

---

## Feature Visibility Checklist

All features below are present in the codebase. Some require environment variables or runtime conditions to activate.

### Always Active (no env var needed)

| Feature | Phase | Description |
|---------|-------|-------------|
| Inline verification markup | 29 | Colored highlights, footnote markers, ClaimOverlay popovers on verified claims |
| 15 MD component overrides | 29 | Custom renderers for code, tables, links, headings, blockquotes, etc. |
| CollapsibleCodeBlock | 29 | Code blocks collapse when >15 lines |
| MessageTOC | 29 | Table of contents for long responses (>3 headings) |
| Drag-drop ingestion | 28 | Drop files on KB pane or chat input |
| Right-click context menus | 28 | Context actions on toolbar icons |
| Search tuning sliders | 28 | Adjustable weights in Settings |
| iPad/tablet responsive | 27 | Sidebar auto-collapse, bottom sheet KB, touch targets |
| Cross-encoder reranking | 32 | ONNX model, 3 modes (cross_encoder / hybrid / disabled) |
| Streaming verification | 29 | SSE-based claim verification with status bar |
| KB context injection | 28 | Select artifacts → inject into chat context |
| Verification status bar tooltips | 34 | Hover explanations for all metrics |
| KB method badge | 34 | Cyan "kb" badge on KB-verified claims |
| Source injection tooltip | 34 | Hover to see injected source names |
| KB Management GUI | 34 | Rebuild indexes, rescore, regen summaries, clear domains |
| Tag Manager | 34 | Merge/browse tags from Knowledge pane |

### Opt-In Features (env var required)

| Feature | Env Var | Default | Description |
|---------|---------|---------|-------------|
| Self-RAG validation | `ENABLE_SELF_RAG` | `true` | Iterative retrieval refinement for weak claims |
| Hallucination check | `ENABLE_HALLUCINATION_CHECK` | `true` | Post-response fact verification against KB |
| Feedback loop | `ENABLE_FEEDBACK_LOOP` | `false` | Save AI responses back to KB |
| Memory extraction | `ENABLE_MEMORY_EXTRACTION` | `true` | Extract facts/preferences from conversations |
| Contextual chunking | `ENABLE_CONTEXTUAL_CHUNKS` | `false` | LLM-generated situational summaries on chunks |
| Auto KB inject | `ENABLE_AUTO_INJECT` | `false` | Auto-inject relevant KB context into queries |
| Model router | `ENABLE_MODEL_ROUTER` | `false` | Smart model selection (recommend/auto modes) |
| Encryption at rest | `ENABLE_ENCRYPTION` | `false` | Fernet encryption for stored content |
| Multi-user auth | `CERID_MULTI_USER` | `false` | JWT auth, tenant context, per-user API keys |
| API key auth | `CERID_API_KEY` | (unset) | X-API-Key header auth when set |

### Retrieval Tuning (env vars with defaults)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| Rerank mode | `RERANK_MODE` | `cross_encoder` | `cross_encoder` / `hybrid` / `disabled` |
| Vector weight | `HYBRID_VECTOR_WEIGHT` | `0.6` | Weight for vector similarity in hybrid search |
| Keyword weight | `HYBRID_KEYWORD_WEIGHT` | `0.4` | Weight for BM25 in hybrid search |
| Chunking mode | `CHUNKING_MODE` | `semantic` | `semantic` or `token` |

### Runtime Conditions

Some features only appear under specific conditions:

- **Inline verification markup**: Only visible after verification streaming completes. Requires a response with verifiable claims.
- **MessageTOC**: Only shown for responses with 3+ markdown headings.
- **CollapsibleCodeBlock**: Only triggers for code blocks with 15+ lines.
- **Model router banner**: Only shown when `ENABLE_MODEL_ROUTER=true` and routing mode is "recommend".
- **Verification re-runs**: Fixed in Phase 34 — cached results persist across tab switches.

---

### Known CVE Ignores

**pip-audit ignores (Phase 11 migration planned):**
- CVE-2026-26013 — SSRF in ChatOpenAI
- CVE-2025-64439 — RCE in JsonPlusSerializer
- CVE-2026-27794 — RCE via pickle fallback

**Trivy ignores (`.trivyignore`):**
- 3 LangChain/LangGraph CVEs (same as above)
- 1 glibc heap corruption (no fix in Debian 13)
- 1 wheel privilege escalation (build-time only)
- 3 libxml2 in Alpine (nginx static files only)
