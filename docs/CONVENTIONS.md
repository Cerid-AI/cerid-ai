# Cerid AI — Conventions

> **Last refresh:** 2026-05-08 (v0.91 release: Python 3.12; defensive imports for internal-only modules in mixed-files; min_length on required identifiers; object-envelope SDK contracts)
> **Scope:** Project-specific style/approach conventions not enforced by lint rules
> **Owner:** New contributors read this first; senior maintainers amend as patterns solidify

Conventions that ARE enforceable by tools live in `.ruff.toml`, `pyproject.toml`, `.github/workflows/ci.yml` (see the drift-gate jobs), `src/mcp/.importlinter`, and the preservation harness. This doc is for the remainder — taste/approach rules you can't spell out as a lint rule.

## Process

- **Never add AI attribution** to commits, PRs, comments, or docs. Commits are authored by the human developer. No `Co-Authored-By: Claude` / `Anthropic` / etc. lines. (Enforced by dotfiles CLAUDE.md; repeated here because it's the most commonly-missed global rule.)
- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every development session.
- **New contributor first steps:** Read this file, then [`docs/ARCHITECTURE.md`](ARCHITECTURE.md), then [`docs/PRESERVATION.md`](PRESERVATION.md). Skip `CLAUDE.md` unless you are an LLM agent — it is written for that audience.

## Architecture

- **Layer boundary is absolute:** `core/` must never import from `app/`, `routers/`, `services/`, etc. Violations fail CI via `import-linter`.
- **Canonical import paths only:** `from core.utils.X import ...`, `from app.routers.X import ...`, `from app.db.neo4j.X import ...`. The old bridge paths (`from utils.X`, `from agents.X`, `from db.neo4j.X`) no longer exist — Sprint E retired `utils/` + `agents/`; the 2026-04-21 Neo4j unification retired the `db.neo4j` shim tree. `lint / no-legacy-neo4j-tree` CI job enforces the last one.
- **New routers live in `app/routers/`** — not `src/mcp/routers/`, which is reserved for `billing.py` (internal-strip target).
- **New agents live in `core/agents/`** if they're portable algorithm logic; in `app/agents/` if they're orchestration wrappers.

## Data & storage

- **ChromaDB metadata is strings/ints only.** Lists stored as JSON strings. Lists-of-lists violate `validate_embeddings`.
- **ChromaDB embeddings return `list[np.ndarray]`** (individual slices, not `.tolist()`) for 0.5.x compatibility.
- **Neo4j Cypher:** use explicit `RETURN` clauses, not map projections (breaks with Python string ops).
- **Deduplication:** SHA-256 of parsed text, atomic via Neo4j `UNIQUE CONSTRAINT` on `content_hash`.
- **Batch ChromaDB writes:** single `collection.add()` call per ingest, not per-chunk.
- **Neo4j auth validation:** `deps.py::get_neo4j()` runs `RETURN 1` (not just `verify_connectivity()`) — empty `NEO4J_PASSWORD` raises `RuntimeError` at startup.
- **Keywords metadata uses `keywords_json`** (JSON-encoded string) consistently across ingest paths. A 2026-03 inconsistency between `keywords` and `keywords_json` caused silent data loss; this name is now canonical.

## Event loop

- **CPU-bound ops offload to `asyncio.to_thread()`:** ChromaDB queries, ONNX embedding, BM25 tokenization, cross-encoder reranking.
- **`/agent/query` is gated by partitioned concurrency pools** (KB/CHAT/HEALTH) — not a process-wide semaphore. `/health` polling never serializes behind chat turns.
- **Frontend auto-inject KB queries** use `AbortController` + 500ms timeout to free browser connection slots before the chat stream fetch fires.

## Verification pipeline

- **One canonical claim shape:** `core.agents.hallucination.models.ClaimVerification`. Every producer emits it; every consumer reads `.artifact_ids()` and `.has_provenance()`. Adapter at the boundary handles legacy dict shapes. See `tests/test_canonical_claim_model.py` for contract.
- **`/agent/hallucination` auto-persists** by default (`persist=True`). Single call produces a fully provenanced `:VerificationReport`. External SDK consumers can opt out with `persist=False`.
- **Three provenance channels:** a saved `:VerificationReport` must carry ONE of:
  1. `[:VERIFIED]`/`[:EXTRACTED_FROM]` edges to `:Artifact` nodes (kb_nli path)
  2. `source_urls` array (web_search path)
  3. `verification_methods` array (cross_model / any path)
  The m0002 migration deletes nodes with all three empty.

## LLM call sites

- **Every `call_internal_llm(...)` takes a `stage=...` breadcrumb** for observability. Stage flows into structlog + Sentry scope. Contract test `tests/test_llm_call_site_contract.py` enforces kwarg validity across all call sites.
- **Model IDs route via the canonical `core.routing.smart_router`** — no bridge paths.
- **Free-tier models have aggressive rate limits** (~8 RPM). Use 8+ second base delay between calls; single 60s retry on 429 instead of exponential backoff (exponential burns quota faster).

## Circuit breakers

- All LLM call sites use breaker names registered in `circuit_breaker.py`.
- Register a new breaker when you add a new call site category.
- **Resetting openrouter-dependent breakers happens together:** `_openrouter_auth_probe_loop()` in `main.py` resets all seven at once on a successful auth probe. Adding a new breaker? Add it to the reset list.

## Rate limiting

- In-memory sliding window, keyed on `X-Client-ID` header (read directly from headers, not `request.state` — middleware ordering independence).
- Per-client limits in `config/settings.py::CLIENT_RATE_LIMITS`. GUI 20/min, trading-agent 80/min, unknown 10/min.
- Test harnesses generate fresh `X-Client-ID=smoke-<tag>-<uuid>` per test to prevent rate-limit collisions.

## Secrets & config

- Single `.env` at repo root, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`.
- `.env`, `.env.age`, `.env.local` never committed — enforced by `_SYNC_SKIP_BASENAMES` in `sync-repos.py` and `.gitignore`.
- **JWT startup validation:** `CERID_MULTI_USER=true` with missing `CERID_JWT_SECRET` raises `RuntimeError` at startup (not just a warning).
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env`. Don't add `${VAR}` interpolation in `environment:` for passthrough vars — empty-env interpolation overrides `env_file` entries.

## Observability defaults

- **Silent failures are observable:** every broad `except Exception:` in hot paths uses `log_swallowed_error(module, exc)` from `core.utils.swallowed`. Surfaces in `/health.invariants.swallowed_errors_last_hour`.
- **Request tracing:** `X-Request-ID` header propagates through every log line via the contextvar filter in `core.utils.request_id`.
- **All observability signals converge at `/health.invariants`** — no scattered secondary health endpoints.

## Docker

- Use `127.0.0.1` not `localhost` in Alpine healthchecks — Alpine resolves `localhost` to `::1` (IPv6), many services bind `0.0.0.0` (IPv4) only.
- Always verify Docker build success — `docker compose build` can return 0 with a cached fallback when the real build exits code 2. Grep the `--progress=plain` output for `error`.

## CI drift gates

- **Adding a new drift-gate CI job?** Decide whether it's deterministic or has environmental variability before picking the rollout shape:
  - **Deterministic checks** (path-existence, pure-Python AST walks, file-hash comparisons) — ship blocking from day one and add to `docker` `needs[]` immediately. No flakiness exposure that soft-gating would absorb. Example: `lint / no-legacy-neo4j-tree` (2026-04-21).
  - **Environmentally-variable checks** (anything that runs `pip-compile`, docker-compose, or a live-stack boot) — ship with `continue-on-error: true`, watch two consecutive green runs on `main`, then flip to blocking. Example: `lint / sdk-openapi-drift` (2026-04-21; four-run wait for extra confidence).
- Either shape must end up blocking in `docker` `needs[]`. Soft-warning CI gates do not exist in this repo by policy (re-check: `grep 'continue-on-error' .github/workflows/ci.yml` should match zero drift jobs).

## Frontend

- **Tailwind v4 via `@tailwindcss/vite` plugin** — no `tailwind.config.ts`.
- **shadcn/ui New York style, Zinc base color**, path alias `@/*` → `./src/*`.
- `crypto.randomUUID()` requires a secure context — on LAN-over-HTTP it's undefined. Use the shared `uuid()` helper in `src/web/src/lib/utils.ts` everywhere instead.
- **`.d.ts` basename must not collide with a `.ts` basename** in the same dir — TypeScript treats the `.d.ts` as a specific module declaration and ignores ambient declarations.

## Design tokens (D.1)

> **Last refresh:** 2026-05-10 (Phase D.1 audit + drift gate)
> **Drift gate:** `scripts/lint-no-design-drift.py` (CI job `lint / no-design-drift`)

### Canonical design tokens

Cerid's design tokens live as CSS custom properties in `src/web/src/index.css`.
Use these in Tailwind classes via the `text-foreground`, `bg-primary`, etc. utility
names — not raw hex values.

**Core semantic tokens** (light + dark mode via `@media (prefers-color-scheme: dark)` or `.dark`):

| Token | Usage |
|---|---|
| `--background` / `bg-background` | Page / app background |
| `--foreground` / `text-foreground` | Primary text |
| `--card` / `bg-card` | Card surfaces |
| `--popover` / `bg-popover` | Popover / dropdown surfaces |
| `--primary` / `bg-primary` | Primary action colour |
| `--secondary` / `bg-secondary` | Secondary action colour |
| `--muted` / `bg-muted` | Muted backgrounds (disabled, subtle) |
| `--muted-foreground` / `text-muted-foreground` | De-emphasised text |
| `--accent` / `bg-accent` | Accent highlights (teal family) |
| `--destructive` / `bg-destructive` | Destructive actions and error states |
| `--border` / `border-border` | Standard border colour |
| `--input` / `border-input` | Form field borders |
| `--ring` / `ring-ring` | Focus ring colour |
| `--brand` / `bg-brand` | Cerid teal brand colour |
| `--brand-foreground` / `text-brand-foreground` | Text on brand-coloured surfaces |

**Chart tokens:** `--chart-1` … `--chart-5` — use via `fill-chart-1` etc.

**Sidebar tokens:** `--sidebar`, `--sidebar-foreground`, `--sidebar-primary`,
`--sidebar-accent`, `--sidebar-border`, `--sidebar-ring`.

**Claim-overlay tokens:** `--claim-verified-border`, `--claim-refuted-bg`,
`--claim-refuted-border`, `--claim-unverified-bg`, `--claim-unverified-border`,
`--claim-evasion-bg`, `--claim-citation-bg`.

**Radius tokens:** `--radius-sm` through `--radius-4xl` — use via `rounded-sm` etc.

### Forbidden patterns

The drift gate (`scripts/lint-no-design-drift.py`) blocks four categories of
violation in `src/web/src/` (`.ts` / `.tsx` files):

1. **Raw hex literals** — `#ff0000`, `#abc`, `#rrggbbaa`. Replace with a CSS-var-backed
   Tailwind token or a named semantic class. Permitted inside CSS files and inside
   `var(--my-token, #fallback)` CSS fallbacks (linter excludes these automatically).

2. **Inline `style={{}}` props** — `<div style={{ width: "100%" }}>`. Use Tailwind's
   arbitrary-value mechanism (`w-full`, `w-1/2`) or a CSS variable. For dynamic values
   that genuinely need an inline style (e.g. a percentage-based progress bar that cannot
   be expressed as a Tailwind token), add `// drift-allowed: <reason>` at the end of
   the line.

3. **Tailwind arbitrary values** — `text-[10px]`, `p-[3px]`, `max-w-[240px]`, `ring-[3px]`.
   Replace with a canonical Tailwind scale step (`text-xs`, `p-1`, `max-w-sm`, etc.) or
   add a design token to `index.css`. The `src/web/src/components/ui/` shadcn directory
   is excluded from this check — shadcn-generated components use `ring-[3px]` etc. by design.

4. **Non-lucide icon imports** — `@heroicons`, `react-icons`, `@material-ui/icons`, etc.
   Use `lucide-react` exclusively. See `components.json` `iconLibrary: "lucide"`.

5. **Non-shadcn motion libraries** — `framer-motion`, `gsap`, `react-spring`, etc.
   Use Tailwind's built-in `animate-*` utilities or Radix's built-in CSS transitions.

### Allowlist / suppression mechanism

Two suppression paths exist for legitimate exceptions:

**Inline suppression** — append `// drift-allowed: <reason>` on the violating line:
```tsx
<ScrollArea style={{ maxHeight }}>  // drift-allowed: dynamic max-height drives scroll region
```

**Allow-file** — pass `--allow-file path/to/allow.txt` to the script. Format: `path:lineno` per line, comments with `#`. Use for multi-line ranges or file-level exceptions.

Neither suppression path is a licence to drift. All suppressions should be reviewed in
code review and ideally tracked as follow-up items against the D.1 punch list
(`tasks/2026-05-10-D1-design-drift-punch-list.md`).

### CI gate

The `lint / no-design-drift` CI job runs `scripts/lint-no-design-drift.py` in
`--report-only` mode — violations are printed to the job log but do not block the build
during the remediation window. Promotion to blocking follows the standard drift-gate
protocol: add to `docker needs[]` and remove `--report-only` after two consecutive
`main` builds show zero violations.

## Plugins & workflows

- Plugins carry a `manifest.json` (name, version, tier, description, entry). BSL-1.1, converts to Apache-2.0 after 3 years.
- Tier gating enforced at load time via `CERID_TIER`.
- Workflow engine uses Kahn's algorithm for DAG validation — cycles rejected.

## Cross-repo sync

- See [`docs/SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md). In short: never `cp`/`rsync` between repos; use `scripts/sync-repos.py`; `validate` before and after every sync.

## When to retire a convention from this file

A convention moves OUT of this file when one of these happens:

1. **A lint rule catches it.** (Example: silent-catch is caught by `scripts/lint-no-silent-catch.py`.) Update the rule; delete the convention.
2. **A contract test enforces it.** (Example: claim shape is guarded by `tests/test_canonical_claim_model.py`.) Keep a one-line reference; delete the detail.
3. **It's specific to one file.** Move to an inline comment; delete here.

CONVENTIONS.md grows by one line per new pattern but should shrink over time as patterns graduate to code-enforced contracts.
