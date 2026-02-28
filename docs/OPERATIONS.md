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
