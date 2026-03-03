# Cerid AI — Operations Guide

> **Created:** 2026-02-28
> **Covers:** API key management, secrets rotation, rate limiting, branch protection, CI pipeline.

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
- **IP-based only:** No per-user or per-API-key rate limiting. All requests from the same IP share a single counter.

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
| `test` | 564 pytest tests, 55% coverage minimum |
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
