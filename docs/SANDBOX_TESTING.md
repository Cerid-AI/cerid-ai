# Sandbox Testing — parallel Cerid install for new-user beta tests

> **Why:** exercising the empty-state / first-run / new-user experience
> without touching your prod KB. A sandbox runs on a separate clone with
> separate volumes, separate `/sync` mount, and (if needed) separate
> ports. Prod's Neo4j / Chroma / Redis data is never at risk.

---

## What a sandbox isolates

| Surface | Default path (prod) | Sandbox path (overridden) |
|---|---|---|
| Repo clone | `~/Develop/cerid-ai/` | `~/tmp/cerid-fresh/` (or anywhere) |
| Neo4j data | `<repo>/stacks/infrastructure/data/neo4j/` | sandbox repo's own subdir |
| Chroma data | `<repo>/stacks/infrastructure/data/chroma/` | sandbox repo's own subdir |
| Redis data | `<repo>/stacks/infrastructure/data/redis/` | sandbox repo's own subdir |
| `/sync` mount (conversations, user state) | `$CERID_SYNC_DIR_HOST` (default: `~/Dropbox/cerid-sync`) | **must** override to a separate dir or the sandbox will see prod's conversations |

The first three are automatic — data paths are relative to the compose
file's directory. The `/sync` mount is the one you must explicitly
override; `docker-compose.yml` reads the host path from
`CERID_SYNC_DIR_HOST` (see `.env.example`).

## Prerequisites

- Docker Desktop running
- An OpenRouter API key (copy from your prod `.env` if you have one)
- Free ports on `8888`, `3000`, `7474`, `7687`, `8001`, `6379` — **OR** stop prod first so the sandbox can reuse them

## Recipe 1 — quick sandbox with prod stopped (15 min)

Simplest and fastest. Prod is temporarily offline; sandbox reuses the
same ports.

```bash
# 1. Stop prod
cd ~/Develop/cerid-ai
docker compose down                    # volumes preserved

# 2. Clone to a sandbox dir
rm -rf ~/tmp/cerid-fresh                # if a previous run exists
git clone ~/Develop/cerid-ai ~/tmp/cerid-fresh

# 3. Prime a sandbox .env with YOUR OpenRouter key + isolated sync dir
cd ~/tmp/cerid-fresh
cat > .env <<EOF
OPENROUTER_API_KEY=$(grep '^OPENROUTER_API_KEY=' ~/Develop/cerid-ai/.env | cut -d= -f2-)
NEO4J_PASSWORD=sandboxpw
REDIS_PASSWORD=sandboxpw
CERID_SYNC_DIR_HOST=/tmp/cerid-fresh-sync
EOF
mkdir -p /tmp/cerid-fresh-sync

# 4. Boot — takes ~2 min (Neo4j + Chroma + Redis + MCP + Web)
./scripts/start-cerid.sh

# 5. Open the UI at http://localhost:3000 (same port as prod)
#    Verify: sidebar shows ZERO conversations, Knowledge page shows 0 artifacts,
#    Settings → System → Server Version is the current release.

# 6. Run your beta test (ingest, query, verify, etc.)

# 7. Teardown
docker compose down

# 8. Restart prod
cd ~/Develop/cerid-ai
docker compose up -d
```

**Warning — Neo4j WAL:** if you `docker compose down` on a data volume
and immediately bring the *same* Neo4j data path back up under a
*different* compose project (e.g. back-to-back prod ↔ sandbox
swaps), the write-ahead log may not checkpoint cleanly. Symptoms:
`DatabaseUnavailable` post-restart; log shows "Checkpoint log file
with version 0 has some data available after last readable log
entry". Recovery: `docker run --rm -v <prod-neo4j-data>:/data
neo4j:5.26.21-community neo4j-admin database check --force=true neo4j`
then restart. See `tasks/lessons.md` → Docker section for the full
writeup.

## Recipe 2 — parallel sandbox (prod stays up)

Run sandbox alongside prod. Needs different ports + separate docker
project name + separate container_names (currently hardcoded — requires
a compose overlay, not yet shipped). Deferred to a future "parallel
sandbox support" sprint; for now, use Recipe 1.

## Recipe 3 — CI-style sandbox (fully isolated, ephemeral)

Used by the preservation-gate CI job. See
`.github/workflows/ci.yml` → `gate / preservation (live stack)` for
the reference pattern: seeds `.env` from `.env.example`, overrides
`NEO4J_PASSWORD` / `REDIS_PASSWORD` / `CERID_SYNC_DIR_HOST` to
CI-writable paths, boots the full stack via
`docker-compose.yml + docker-compose.ci.yml`. Teardown is
`docker compose down -v` (drops volumes too).

## What the sandbox is good for

- **First-run setup wizard UX** — API-key missing → wizard pops, user
  pastes a key, `.env` is written, services restart, app becomes usable.
- **Empty-state UI** — Knowledge page with 0 artifacts, "no ingestion
  activity yet", drop-zone affordance.
- **Ingest flow** — drop a PDF/MD/DOCX, watch SSE progress, confirm the
  artifact + chunks appear in Chroma.
- **First query against a populated sandbox KB** — verification pipeline
  fires, claims extract, per-claim verdicts render.
- **Governance switches** — flip `STRICT_AGENTS_ONLY=true` or
  `MCP_CLIENT_MODE=disabled`, restart MCP, observe the UI reaction.

## What the sandbox is NOT good for

- Load tests — single-machine Docker isn't representative of prod
  capacity; use `make slo` against the CI benchmark-slo gate.
- Multi-user regression — the middleware honors JWT auth, but the
  sandbox defaults to `CERID_MULTI_USER=false`. Set it to `true` if
  you need the multi-user surface.
- Data-migration testing — sandbox starts empty. For migration tests,
  use `scripts/run_migrations.py` against a populated fixture.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Sandbox sidebar shows prod's conversations | `/sync` mount not isolated | Set `CERID_SYNC_DIR_HOST=/tmp/cerid-fresh-sync` in sandbox `.env` and re-boot |
| Neo4j `DatabaseUnavailable` after prod↔sandbox swap | WAL not checkpointed cleanly | See "Warning — Neo4j WAL" above |
| Port conflict on 8888 / 3000 | Prod still running | `docker compose down` in the prod dir first |
| `OPENROUTER_API_KEY=` empty in sandbox `.env` | Recipe 1 step 3 didn't read prod `.env` | Check prod `.env` has the key; paste manually if needed |
| Ingest succeeds but `/agent/query` returns 0 KB | **Fixed 2026-04-23** — verify you're on a commit after `d79b801` (internal) / `cb7abcf` (public); older checkouts need the tenant-scope fix |

## Related

- `tasks/lessons.md` → Docker section — Neo4j WAL recovery procedure
- `.github/workflows/ci.yml` → `gate / preservation` — reference CI-sandbox setup
- `docs/SYNC_PROTOCOL.md` — internal↔public repo sync (not related to `/sync` user-state mount — different subsystem)
