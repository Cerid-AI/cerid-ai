# Beta test harness

`./tests/beta/run.sh` orchestrates the Docker-stack test tiers (smoke,
functional, integration, performance, security, browser E2E, eval — see
the usage comment at the top of `run.sh`).

## Desktop smoke (packaged app)

`--desktop` is a separate, opt-in tier: a first-run smoke of the
**packaged** macOS app (`/Applications/Cerid AI.app`, or `$CERID_APP` to
point at another build), driven over the Chrome DevTools Protocol rather
than a dev server. It is not part of the Docker-stack tiers and not run
by `--full`.

```bash
./tests/beta/run.sh --desktop            # smoke the installed app
./tests/beta/run.sh --desktop --reset    # also wipe this Mac's saved
                                          # connection and setup-complete
                                          # state first, to force the
                                          # first-run flow
```

Equivalently: `./tests/beta/desktop-smoke.sh [--reset]`.

**Needs:**
- Cerid AI.app installed (or `$CERID_APP` pointing at a build).
- The personal stack reachable at `http://localhost:8888`.
- `CERID_API_KEY` set in the environment or in the repo's `.env`.

**What it does:** launches the app with `--remote-debugging-port=9222`,
drives it through first-run (typing the key from `.env`, testing and
saving the connection, checking the macOS-permissions step, sending one
chat message), quits and relaunches it to confirm the setup persisted,
then deletes the one conversation it created and quits the app. Each
check prints a `PASS`/`FAIL`/`SKIP <name>` line. If the Mac was already
set up when the run started, the script says so and skips the checks
that would otherwise re-run first-run against a real, already-configured
install — pass `--reset` to force a clean first-run instead (it removes
`connection.json`, `secrets/`, and `Local Storage` — the last of which
holds the "already set up" flag, independent of the connection itself).

App and check logs land under `tests/beta/reports/` (gitignored).

The parts of the smoke that need no packaged app — argument handling, the
app-absent failure, and the ordering that keeps `--reset` away from real state
when the app is missing — are pinned by
`tests/beta/desktop/smoke-args.test.sh`, which runs anywhere:

```bash
bash tests/beta/desktop/smoke-args.test.sh
```

## Smoke tests

`smoke.sh` is the P0 gate for `run.sh` — it must pass before any other tier
runs. Each check is host-side (no container exec beyond `docker inspect` /
`docker exec`) and writes one line to `reports/smoke.results`.

| ID | Checks |
|----|--------|
| S-01 | Docker containers running (`ai-companion-mcp`, `ai-companion-neo4j`, `ai-companion-chroma`, `ai-companion-redis`) |
| S-02 | MCP `/health` returns `status: healthy` |
| S-03 | ChromaDB v2 heartbeat |
| S-04 | Neo4j HTTP reachable |
| S-05 | Redis ping |
| S-06 | Frontend reachable |
| S-07 | MCP `/collections` endpoint |
| S-08 | `scripts/validate-env.sh --quick` |
| S-09 | Gateway `/api/bifrost/*` returns 404 |

## S-09: Bifrost route removed

`stacks/gateway/Caddyfile` used to proxy `/api/bifrost/*` to a `bifrost`
service that no compose file has defined since 2026-04-17 — every request
502'd against a dead upstream. The block is gone from the Caddyfile.
Unknown API paths return 404 at the gateway: a `handle /api/*` block after
every specific `/api/...` handle answers unmatched `/api/*` paths with 404
before Caddy's routing reaches the SPA catch-all, so `/api/bifrost/*` (and
any other unrecognized `/api/*` path) no longer falls through to `cerid-web`'s
`index.html`.

The check:

- Skips with a reason when the `cerid-gateway` container isn't running.
- Otherwise sends `GET https://127.0.0.1:443/api/bifrost/x` with `-k` (the
  gateway's public site uses Caddy's self-signed `local_certs`) and an
  `X-API-Key` header (clears the gateway's SSO `forward_auth`, same as any
  programmatic client — see `stacks/gateway/sso/sso.py`), asserting **404**.
- Reads `CERID_API_KEY` from the shell, falling back to the repo `.env`,
  same convention as `REDIS_PASSWORD` above it in `smoke.sh`.

This check only turns green once `cerid-gateway` is restarted onto the
edited Caddyfile — see the task report for the current (old-Caddyfile) run.

## Compose project targeting

`tests/beta/lib/assert.sh` — the shared lib every tier sources — exports
`COMPOSE_PROJECT_NAME` once, derived from the main checkout's directory name
via `git rev-parse --git-common-dir`, not the caller's cwd. Every `docker
compose` call in `tests/beta/*.sh` (currently S-01's `docker compose ps` in
`smoke.sh`) inherits it, so the harness finds the running stack's containers
even when it's invoked from a worktree whose own basename compose would
otherwise default to.

`tests/beta/test_compose_project.sh` proves this: it creates a throwaway
worktree with `git worktree add`, runs S-01 from inside it, asserts PASS,
and removes the worktree with `git worktree remove` afterward.
