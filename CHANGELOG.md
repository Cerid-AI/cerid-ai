# Changelog

All notable changes to cerid-ai are documented here.

## v0.93.5 — Chat virtualization + L4 backend enforcement + Dependabot batch (2026-05-12)

Three-in-one release closing the open-action queue from v0.93.4.  Bundle
chosen for atomicity (one merge, one CI run, no per-PR triage) over a
trickle of patches.

**Chat message virtualization** (v0.84.0 deferral cleared, Cycle 3.2 follow-on)

* `src/web/src/components/chat/chat-messages.tsx` refactored into a
  dispatcher with two parallel implementations sharing a single
  `<MessageRow>` so the plain `.map()` branch and the new
  `<VirtualizedChatMessages>` branch can't drift in their per-message
  rendering.  `@tanstack/react-virtual@3.13.24` is pinned at an exact
  version published **24 days before the May 11 2026 supply-chain
  attack** on the `@tanstack/router` family (GHSA-g7cv-rxg3-hmpx); neither
  `@tanstack/react-virtual` nor `@tanstack/virtual-core` was in the
  affected list, and pinning blocks any future re-resolution from
  picking up an attacker-injected version.
* Feature flag `useChatVirtualization()` reads
  `localStorage['cerid:chat-virtualized']` first, then the
  `VITE_CHAT_VIRTUALIZATION` env var, default OFF for the v0.93.5
  release.  The recommender engine surfaces the toggle once a user's
  longest conversation crosses 200 messages (second user of the
  C3.2 adaptive recommender — first since SPLADE).
* Auto-scroll integration replaces the plain branch's
  `viewport.scrollTop = viewport.scrollHeight` pixel math with
  `virtualizer.scrollToIndex(messages.length - 1, { align: "end" })`.
  The user-scrolled-up heuristic switches to comparing the last
  virtual index against the total count — same semantics, virtualizer-
  aware implementation.
* jsdom polyfill in `src/__tests__/setup.ts` shims both
  `Element.getBoundingClientRect` and `clientHeight`/`clientWidth` for
  the Radix ScrollArea viewport so the virtualizer can compute its
  visible window under tests.  Unit tests verify dispatcher logic +
  empty-state guard + render-tree wrapper presence; visible-window
  clipping is verified via the manual-browser sign-off in
  `docs/plans/2026-05-12-chat-virtualization-sprint-plan.md`.

**L4 backend enforcement** (closes the architectural privacy-contract gap)

The UI has rendered Private Mode L4 ("Full ephemeral") since v0.92.1
but the backend validator was capped at `le=3`, leaving the
wipe-on-close contract half-shipped.  v0.93.5 closes the gap:

* `PrivateModeRequest.level` now accepts `0–4`.
* New `POST /settings/private-mode/session-wipe` endpoint, idempotent,
  scoped to `conversation_id`.  Clears the global flag + the per-session
  override.
* Frontend wires `wipePrivateSession()` via `navigator.sendBeacon` on a
  `beforeunload` handler registered in `useSettings` when
  `privateModeLevel === 4`.  Falls back to `keepalive: true` fetch when
  `sendBeacon` is unavailable (jsdom / older browsers).
* 9 new pytest cases cover L0–L4 acceptance, idempotency, scope, and
  invalid-input rejection.

**Dependabot batch** (11 PRs absorbed locally — one CI run instead of 11)

Applied directly to main rather than per-PR merges to save GitHub
Actions minutes and bundle the dep-graph state with the release:

* `fastapi 0.135.4 → 0.136.1`
* `bm25s 0.3.4 → 0.3.8`
* `structlog 24.x → 25.5.0`
* `python-multipart 0.0.18 → 0.0.28`
* `pytest-benchmark 4.0 → 5.2.3`
* `sonner 1.7.4 → 2.0.7`
* `actions/checkout@v4 → @v6` (all three workflow files)
* `actions/setup-python@v5 → @v6` (all three workflow files)
* `actions/upload-artifact@v4 → @v7`
* `actions/download-artifact@v4 → @v8`
* `aquasecurity/trivy-action@0.35.0 → @0.36.0` (SHA-pinned)

The TanStack supply-chain attack was investigated mid-flight — the
project's existing `@tanstack/react-query@5.100.10` + new
`@tanstack/react-virtual@3.13.24` + `@tanstack/virtual-core@3.14.0` are
all clean (not in the 42-package affected list).

## v0.93.4 — Sidecar SPLADE endpoint + Private Mode polish + chat-virtualization sprint plan (2026-05-12)

Closes the C3.2 follow-on punch list and a long-deferred UX polish round.

**Sidecar `/encode/sparse` endpoint** — the server-side companion to the client
shipped in v0.93.3 (`utils/inference_sidecar_client.py:sidecar_encode_sparse()`).
`scripts/cerid-sidecar.py` gains a SPLADE-v3 loader that lazy-initializes on the
first `/encode/sparse` call (so operators who never enable sparse pay no
cold-start cost), picks between full-model and bolted-MLM-head branches by
inspecting `session.get_outputs()`, and mirrors the in-process encoder's exact
numpy formula so the sidecar fast-path and local-ONNX fallback stay
wire-identical. Health endpoint reports `sparse_loaded` + `sparse_branch` for
observability. 10 new pytest cases use `importlib` to load the script and
mock the ONNX session, so CI runs in milliseconds without network.

**Private Mode UX polish** — three load-bearing improvements to the
v0.84.0 engine surfaced after the v0.92.1 round closed:

* **Bottom-of-menu data-route footnote** — clarifies what "saved" means
  at each level. L0 → server + local cache. L1 → local cache only. L2 →
  also bypass KB injection. L3 → also skip Redis audit logs. L4 →
  in-memory only, gone with the tab. Replaces a documentation gap users
  had flagged: per-level descriptions told you what was OFF but not where
  the data actually lived.
* **L4 persistent "wipe on close" hint** — the badge for Level 4 now
  carries an always-visible tooltip explaining the lifecycle, so users
  who dismissed the AlertDialog confirmation don't lose the contract.
* **Read-only Privacy section in Settings** — a new Essentials-tab card
  shows the active level (Off / L1–L4) with a color-coded badge matching
  the chat toolbar, plus a one-line explanation of what the active
  level does. The level itself is changed from the chat toolbar so a
  single privacy state lives next to the live conversation, not behind
  a Settings tab.

Skipped from the polish round: the "color-code overflow menu Private
toggle" candidate flagged by the scoping pass was actually already
shipped — `chat-toolbar.tsx:666-671` carries the same color logic as
the main toolbar. Skipped to avoid duplicate work. The L4-backend
enforcement gap (API accepts `le=3`; UI renders L4 as a fully ephemeral
contract enforced client-side only) is an architectural privacy-contract
change, not polish, and is flagged for a separate cycle.

**Chat virtualization sprint plan** — published at
`docs/plans/2026-05-12-chat-virtualization-sprint-plan.md`. Scoping
concluded this is a 3–5 contiguous-day sprint, not an inline pass.
Half-shipping would break streaming auto-scroll. The plan locks
`@tanstack/react-virtual` + the `data-index` anchor strategy + a
feature-flag rollout, and notably the recommender entry that will
surface the toggle once a user's longest conversation crosses 200
messages — making this the second user of the C3.2 adaptive-recommender
engine. Target ship: v0.94.0.

## v0.93.3 — SPLADE-v3 sparse retrieval + adaptive recommender (RAG Cycle 3.2, 2026-05-12)

Re-entry on C3.2 (the sparse-retrieval phase that was honestly deferred in v0.93.2). Ships
the third retriever alongside the existing dense bi-encoder + BM25, fused via the
existing N-way `rrf_fuse`. Adds a **general adaptive-recommendation engine** as net-new
infrastructure — SPLADE-v3 is the first user; HyPE, parent-child, and RRF piggyback on
the same surface at no extra UI cost. The recommender surfaces a dismissable banner in
the Settings pane once the operator's corpus crosses a feature-specific threshold.

Plan doc: [`docs/plans/2026-05-12-c3-2-sparse-retrieval-plan.md`](docs/plans/2026-05-12-c3-2-sparse-retrieval-plan.md).

**SPLADE-v3 encoder + index** — `core/retrieval/sparse.py` ships a lazy-init thread-safe
ONNX encoder that picks at init between two execution branches: full-model exports
(head baked into the graph) and backbone-only exports (head bolted in numpy from the
MLM `decoder.weight` cached as `.npz`). `core/retrieval/sparse_index.py` clones the
`bm25.py` shape — per-domain JSONL, fsync-on-append crash safety, tenant scoping at
the index layer, corrupted-line tolerance. Pivoted from BGE-M3 (the original spike
target) to SPLADE-v3 after literature review: smaller (~140 MB vs 2.16 GB), faster
(~50–100 ms vs 227 ms per doc), and the head is a trivial
`log(1 + ReLU(max-pool(MLM_logits)))`.

**3-way RRF wire-in** — `core/agents/query_agent.py` now fires a third
`asyncio.to_thread(search_sparse)` task in parallel with vector + BM25 when
`HYBRID_FUSION_MODE=tri_rrf`. Zero-cost when the flag is off — no encoder load, no
JSONL probe.

**Ingest-time indexing** — `app/services/ingestion.py` patches both BM25 call sites
with a guarded `sparse_index.index_chunks()` invocation. Sparse exceptions never break
the two-phase commit.

**Adaptive recommendation engine** — `core/config/recommendations.py` declares a
4-entry registry (`sparse_retrieval`, `hype_indexing`, `parent_child_retrieval`,
`rrf_fusion`) each with an env-var-tunable threshold and a `reason_template` that
substitutes the live corpus size. `app/processor/jobs/config_recommender.py` runs
every 6 hours via APScheduler — pulls non-eval-corpus Artifact count from Neo4j,
walks the registry, writes the `cerid:recommendations` Redis hash. `/health` surfaces
the live entries as `recommended_features`, filtered by the per-tenant
`cerid:recommendations:dismissed:{tenant}` set. The Settings-pane banner polls every
60 s and offers three actions: Enable now (PATCH + clear), Maybe later (sessionStorage
snooze), Dismiss permanently (server-side per-tenant).

**Settings router** — `enable_sparse_retrieval`, `hybrid_fusion_mode`
(`"weighted_sum" | "rrf" | "tri_rrf"`), and `hybrid_rrf_sparse_weight` exposed via
PATCH/GET. The sparse toggle mutates `os.environ["RETRIEVAL_SPARSE_ENABLED"]` and
`core.retrieval.sparse.SPARSE_ENABLED` live so the next ingest/search call sees the
new state. Toggling sparse on auto-picks `tri_rrf` in the UI; the segmented control
stays exposed for revert.

**Tests** — 59 new pytest cases (encoder degradation, index roundtrip with tenant
scoping + corrupted-line tolerance, 3-way RRF, ingest guards, recommender pass +
idempotency, /health filter + dismiss/clear round-trips, settings router PATCH/GET).
5 new frontend vitest cases (banner renders, Enable now, Maybe later snooze, Dismiss
permanently). All gates clean: ruff / mypy / import-linter / silent-catch lint / tsc /
eslint / vitest 1088 pass / pytest.

**Sidecar follow-on** — `utils.inference_sidecar_client.sidecar_encode_sparse()` client
shipped; the matching `/encode/sparse` server endpoint is a follow-on PR. The
local-ONNX fallback works without the sidecar.

**Eval gate** — parked at 20-doc dev corpus (same recall-saturation note as HyPE).
The wiring is the v0.93.3 deliverable; the gate flips post-corpus-growth per the
`docs/EVAL_BASELINES.md` procedure.

## v0.93.2 — Bidirectional vault (RAG Cycle 3, 2026-05-12)

Closes the Obsidian-integration loop started in v0.93.1. Cerid's
outputs — chat distillations, synthesis briefs, weekly digests — can
now write back to the user's vault as markdown notes via
`POST /wiki/write_note`, and assistant chat messages gain a
**"Save to vault"** action button. Sparse retrieval was the third
intended C3 deliverable; the BGE-M3 spike found a real blocker that
warrants its own focused cycle, and that path was deferred honestly
rather than shipped half-faked.

**Two-way write API** (`610a04a`) — `POST /wiki/write_note` accepts
`{vault_id, path, content, frontmatter?, mode?: "create"|"append"|
"overwrite", allow_synthesis_input?}` and returns
`{file_path, artifact_id, ingested, frontmatter_written, mode}`. After
the write, the note is automatically re-ingested as an Artifact with
`source_type="cerid-synthesis"` so it's queryable. Path safety reuses
C2.3's `VaultProfile.classify_path` — `..` escapes rejected; templates
and skip folders rejected. Atomic write via tmp + os.replace.

**Loop-breaker** — every Cerid-written note gets stamped
`source_type="cerid-synthesis"` on both the ChromaDB chunk metadata
AND the Neo4j Artifact node properties. Brief and weekly-synthesis
jobs now filter their input claims to exclude `cerid-synthesis`
artifacts by default. A note with `cerid:reanalyze: true` in
frontmatter (OR the service-level `allow_synthesis_input=True` kwarg)
opts back INTO synthesis inputs — the explicit "re-analyze/update"
carve-out.

**Synthesis writeback** (`77a2ebf`) — daily brief + weekly synthesis
processor jobs can opt to write their generated content to a registered
vault. Off by default; configured via `GET/PUT /briefs/settings` and
the new BriefSettingsSection in the Settings panel. Default filenames
`_briefs/brief-YYYY-MM-DD.md` / `_briefs/synthesis-YYYY-MM-DD.md`,
mode=append (forgiving / idempotent). Vault-write failure inside the
job is swallowed via `log_swallowed_error` — never fails the brief.

**Chat save-to-vault UI** — assistant messages get a button next to
the existing copy action. Opens a dialog with vault selector (populated
from `/watched-folders?is_vault=true`), path input (default
`chat/{conversation-title}-{message-id}.md`), mode radio, content
preview, and a Save button.

**C3.1 spike + C3.2 deferral** (`9b55b44`) — `scripts/bge_m3_spike.py`
validated every public BGE-M3 ONNX export. Result: all four shipped
exports (`BAAI/bge-m3`, `Xenova/bge-m3`, `aapot/bge-m3-onnx`,
`hooman650/bge-m3-onnx-o4`) are `XLMRobertaModel` backbone only — the
sparse-weights, dense-projection, and ColBERT heads are NOT in the
graph. `session.get_outputs()` returns one tensor:
`last_hidden_state [B, T, 1024]`. The C3.2 blueprint's `outputs[1]`
does not exist. Mean encode latency 226.7 ms/doc on M-series CPU
(extrapolates to 6.3 hrs at 100K-chunk scale). Re-entry options for a
future cycle: (a) custom-export FlagEmbedding's BGEM3FlagModel with
the sparse head attached, or (b) switch to SPLADE-v3 which has working
ONNX exports. The full spike findings are in
`docs/EVAL_BASELINES.md`'s phase ledger.

**Audit fixes** (pre-tag review caught 2 issues):
- `vault_written` in JobResult.metadata now reflects the actual write
  outcome rather than just "branch entered" — `_vault_write_brief` /
  `_vault_write_synthesis` return a bool and the runner assigns from
  that return.
- C3.2 deferral is now durably recorded in
  [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md) (committed, syncs
  to public) so future operators inherit the context — the gitignored
  spike doc alone wasn't enough.

**Verification** — 3 commits, ~3000 LOC across ~20 files (backend +
frontend). All gates green:
- ruff + mypy + import-linter + lint-no-silent-catch on full src/mcp/
- 18 new vault-write + synthesis-writeback tests + 5 frontend tests
  + 280 regression tests on adjacent brief/wiki/vault surface
- tsc + eslint + vitest clean on frontend
- router-registry regenerated for the new `/wiki/write_note` +
  `/briefs/settings` routes

## v0.93.1 — Obsidian-style integration layer (RAG Cycle 2, 2026-05-12)

Six-phase integration on top of the existing RAG pipeline: wikilink
parsing, frontmatter, vault source profiles, recursive email-attachment
ingestion, three small parser improvements, and end-to-end parent-child
retrieval. The cycle treats markdown as graph signal rather than plain
text — links, aliases, and folder semantics now flow into Neo4j as
first-class relationships.

**Wikilinks** (`3696967`) — `[[Some Note]]`, `[[Note|alias]]`,
`[[Note#heading]]`, and `![[embed.png]]` are now parsed during the
existing layout-aware chunking pass and materialized as
`(:Artifact)-[:WIKILINKS_TO]->(:Artifact)` /
`(:Artifact)-[:EMBEDS]->(:Artifact)` edges in Neo4j. Broken links
create `(:PendingArtifact {name})` placeholders that get promoted
when the target is later ingested. New files:
[`core/ingest/wikilinks.py`](src/mcp/core/ingest/wikilinks.py),
[`app/db/neo4j/wikilinks.py`](src/mcp/app/db/neo4j/wikilinks.py).
ReDoS-safe regex with 50 KB input cap and code-fence stripping.

**Frontmatter** (`8f66184`) — YAML frontmatter is now extracted and
allowlisted into artifact metadata. Reserved keys (`tags`, `aliases`,
`cssclass`, `status`, `created`, `updated`, `source`) flow through
existing pipelines; any `cerid:*`-prefixed custom key lands as a Neo4j
node property (with non-alphanumerics sanitized to underscore).
Aliases now feed the wikilink resolver — `aliases: [Foo]` promotes
any `PendingArtifact {name: "Foo"}`. New
[`core/ingest/frontmatter.py`](src/mcp/core/ingest/frontmatter.py).

**Vault source profile** (`5bcd845`) — folders registered as vaults
now apply folder semantics: `mocs/` → `sub_category="moc"`, `daily/`
→ `sub_category="daily"`, `templates/` → SKIP, `attachments/` →
ingest binaries bypassing the general extension allowlist. Dual-source
config: `.cerid-vault.yaml` in the vault root takes precedence, with
the Settings UI form as fallback. New
[`core/ingest/vault_config.py`](src/mcp/core/ingest/vault_config.py) +
[`components/settings/vault-config-section.tsx`](src/web/src/components/settings/vault-config-section.tsx)
+ `GET /watched-folders/{id}/vault-profile` endpoint.

**Email-attachment recursive ingestion** (`f500dd4`) — `parse_eml`
and `parse_mbox` now extract attachment bytes and ingest each
attachment as its own Artifact via the existing parser dispatch
(PDFs go to the PDF parser, DOCX to office.py, etc). Parent →
attachment is materialized as `(:Artifact {source_type:"email"})
-[:HAS_ATTACHMENT]->(:Artifact)`. 50 MB cap per attachment; magic-byte
mismatch skips; one bad attachment never aborts the batch; nested
.eml-in-.eml is captured as text only (cycle prevention via
`_SKIP_NESTED_ATTACHMENTS` ContextVar). Dedup'd attachments
re-link via HAS_ATTACHMENT instead of creating duplicates.

**Parser improvements** (`f7ab333`) — three small wins bundled:
- mbox truncation surfaced as `mbox_truncated` /
  `mbox_total_messages` / `mbox_message_cap` on the parse response;
  new `MBOX_MESSAGE_CAP` env var (default 100). A 10K-message mbox
  no longer silently loses 99% of content.
- PPTX parser added via `python-pptx` (slide-by-slide text + notes).
  Legacy `.ppt` raises a clear `HTTPException(422)` with conversion
  guidance instead of a generic crash.
- MSG parser added via `extract-msg`; mirrors the `parse_eml`
  contract including AttachmentBlob recursion. `.pst` is deferred
  honestly — needs system `libpff` or the existing `pst-scanpst`
  sidecar wired up; that's a focused phase of its own.

**Parent-child retrieval end-to-end** (`4c6d989`) — the long-dormant
`ENABLE_PARENT_CHILD_RETRIEVAL` flag is now actually wired. Audit
found the feature was a non-functional skeleton: the chunker helper
existed but neither the ingest path nor the query path called it.
C2.6 lights it up: when the flag is true, ingest writes both parent
(~512 token) and child (~128 token) chunks with `chunk_level`
metadata + `parent_chunk_id` linkage; vector retrieval ranks against
children for precision, then substitutes parent text into the top-K
results before reranker and LLM context. When the flag is off, every
chunk gets `chunk_level="child"` so the metadata field is uniformly
present — no runtime branching needed. Eval gate deferred per the
documented procedure; default stays OFF until corpus growth exposes
recall headroom.

**Audit fixes** — pre-tag review caught 4 issues:
- `CERID_RSS_POLL_INTERVAL` was defined twice in `settings.py` with
  conflicting units (seconds vs minutes); duplicate removed.
- Wikilink `source_chunk_id` resolved against the flat `chunk_ids`
  list — pointing at parent rows when parent-child was on. Now
  resolves against `child_chunk_ids` so the edge always references
  a retrieval-visible chunk.
- `cerid:*` frontmatter keys with non-alphanumeric characters
  (spaces, dots) now sanitize cleanly to a legal Neo4j property
  identifier instead of failing the inline Cypher.
- `_substitute_parent_content` no longer mutates result dicts in
  place — returns a fresh list so future shared/cached result paths
  can't be corrupted.

**Verification** — 6 phases, ~30 files, ~3500 LOC. All checks green:
ruff + mypy + import-linter + env-example-drift across full
src/mcp/ tree. Live-Neo4j tests verified against the sandbox stack
(127.0.0.1:8898) for every Neo4j-touching phase: 5 wikilink tests +
4 frontmatter tests + 3 HAS_ATTACHMENT tests + 5 parent-child
end-to-end tests all pass.

## v0.93.0 — HyPE wiring fix (Workstream E R.3 lands, 2026-05-12)

Fixes three integration bugs that had been silently preventing the HyPE
retrieval augmentation from ever building per-domain indexes despite the
flag being available since Phase R.3. Discovered while attempting to
clear the HyPE eval gate documented in [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md).

**Wiring fixes (`d03622c`)**

- `HyPEIndexingJob` was not imported in
  [`app/processor/jobs/__init__.py`](src/mcp/app/processor/jobs/__init__.py),
  so `build_default_registry()` never discovered it. Every enqueue logged
  "unknown job_type: 'hype_indexing'" and the worker marked the job
  failed. Added the import + `__all__` entry.
- [`enqueue_job()`](src/mcp/app/db/redis/processor_queue.py) built the
  `JobRecord` via `job.new_record()` with no payload arg, so
  `JobRecord.payload` was always `{}`. The worker re-instantiates jobs
  as `job_class(**record.payload)` and crashed with "instantiation
  error: missing 4 required positional arguments". Helper now accepts a
  `payload=` kwarg; the HyPE enqueue site at
  [`services/ingestion.py:186`](src/mcp/app/services/ingestion.py)
  passes the constructor args through.
- [`services/hype_indexer.py:132`](src/mcp/app/services/hype_indexer.py)
  passed the collection name positionally to
  `chroma.get_or_create_collection`, but the `_EmbeddingAwareClient`
  wrapper in [`app/deps.py:84`](src/mcp/app/deps.py) only accepts
  kwargs. Switched to `name=...`.

**Sibling fix (`abe8748`)**

- Caught during C1 audit: [`scripts/backfill_entities.py:293`](src/mcp/scripts/backfill_entities.py)
  was enqueueing `EntityExtractionJob` without a payload via the same
  helper that bug #2 above fixed for HyPE. Identical failure mode —
  silent crash on every dequeue. Now passes payload.

**Eval ledger row (`f34eedb`)**

- Captured HyPE-on metrics against the seeded 20-doc eval corpus once
  the pipeline actually worked. All 127 chunks produced HyPE indexes
  across 5 per-domain collections in <30 min. IR metrics tied baseline
  exactly (recall@10/MRR/NDCG@10/NDCG@5/precision_5 all 0.000 delta —
  recall is saturated at this corpus size). Latency p50 -108ms,
  p95 +640ms (+17.6%, within the +30% budget), p99 -102ms. The
  decision rule requires lift on at least one gated metric AND latency
  within budget; HyPE passes the latency half but fails the lift half.
- `RETRIEVAL_HYPE_ENABLED=false` remains the default. Opt-in via env
  var now works end-to-end. Re-evaluate at 100+ documents — same
  "small-corpus saturation" pattern as Phase 3a (RRF) and 3b
  (contextual chunks). Full ledger row at
  [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md).

**Verification**

- All 28 HyPE-adjacent tests pass (`test_hype_indexing_job.py`,
  `test_hype_indexer.py`, `tests/integration/test_r3_hype_eval_gate.py`).
- `ruff check src/mcp/` clean; `mypy` clean on modified files.
- End-to-end validated against the sandbox stack: HyPE jobs reach
  `state=completed`, 5 `domain_*_hype` collections appear in ChromaDB.

## v0.92.2 — UI Audit Phases 1–9 (Comprehensive, 2026-05-11)

> Tag superseded mid-day. The initial v0.92.2 cut (commit `7122ec6`) shipped
> only audit phases 1 + 2 (visible-bug ledger + reduced-motion accessibility).
> This expanded cut adds phases 3–9 in three orchestrated waves so the
> v0.92.2 tag captures the complete audit response.

Comprehensive response to the UI/UX audit at
[`tasks/2026-05-11-ui-audit.md`](tasks/2026-05-11-ui-audit.md). All 8 P0
visible bugs cleared, 24 P1 quality issues closed, 7 P2 polish opportunities
landed. Three new primitives extracted; verification + chat surfaces
refactored; 8 motion enhancements added; light-mode brand-shine + status-bar
gold both made theme-aware.

**Wave 1 — Primitive extraction + 10 site migrations** (`feature-dev:code-architect`
agents A + B; integrated by hand)

- New primitive [`<PaneError>`](src/web/src/components/ui/pane-error.tsx) —
  sibling to `<EmptyState>` with inline + `fullPage` forms, optional retry,
  axe-clean. Adopted across 8 panes (Wiki, Communities/GraphExplorer,
  Processor, Monitoring, Knowledge, Audit, Memories, Wiki entity detail).
  The Processor pane now returns early when `statusError` is true (the
  prior "Cerid Idle badge + empty tabs + destructive Alert" three-signal
  confusion is gone).
- New primitive [`<SegmentedControl>`](src/web/src/components/ui/segmented-control.tsx)
  — single-select radiogroup styled as a connected button row, with full
  keyboard navigation (ArrowLeft / ArrowRight with wrap). Adopted in
  `audit-pane.tsx` for the 1h / 6h / 24h / 7d / 30d time-range row.
- New primitive [`<TierSelector>`](src/web/src/components/ui/tier-selector.tsx)
  — three-card radiogroup with locked-state + Pro badge. Adopted in
  `settings-pane.tsx` (Quick / Balanced / Maximum) and
  `pipeline-section.tsx` (Efficient / Balanced / Maximum retrieval preset).
- Three TanStack hooks (`useWikiEntities`, `useWikiEntity`, `useCommunities`,
  `useCommunity`, `useProcessorStatus`, `useProcessorRecent`) now expose
  `refetch` so the adopt-sites can wire retry CTAs.
- 29 new unit tests across the 3 primitives (axe-clean via jest-axe).

**Wave 2 — Verification + chat surface refactors** (general-purpose agents
C + D, parallel; disjoint file scopes — no merge conflicts)

*Verification surface (Phase 4):*
- **V-P0.1 Nested `<button>` HTML violation** in `<VerificationStatusBar>`
  summary row eliminated — outer container is now a `<div role="button"
  tabIndex={0}>` with `onKeyDown` for Enter/Space; expand-toggle is a
  dedicated end-of-row `<button>`. Caught live as a dev-console warning
  during the audit and reproduced under React 19 strict-mode.
- **V-P0.2 `<ClaimOverlay>` migrated to Radix `<Popover>`** — drops the
  `popoverHeight = 220` magic constant + manual `getBoundingClientRect`
  flip logic. Radix handles positioning, collision detection, focus
  management, and the animation. `PopoverAnchor` re-exported from
  `components/ui/popover` to support virtual-anchor placement at the
  click rect.
- **V-P0.3 Green-on-green light-mode contrast** in the claim-overlay
  "Found answer" section fixed (`text-green-300/80` → `text-green-800
  dark:text-green-300/80`).
- **V-P1.4 TrustScore dual-trigger** dropped — chip is now click-only with
  a downward `<ChevronDown>` glyph + `aria-haspopup="dialog"`. HoverCard
  removed.
- **V-P1.5 TrustScore modal score-in-DialogTitle** moved to a dedicated
  sub-row beneath the title; band span gained the missing `borderClass`.
- **V-P1.7 + V-P1.8 Entity detail** — raw brand tokens (`bg-brand/10
  text-brand`) on the refresh spinner replaced with semantic
  (`bg-primary/10 text-primary`); refresh spinner now *replaces* the
  `<ConfidenceBandBadge>` rather than rendering alongside it.
- **V-P2.2 TrustScore sparkline placeholder** removed entirely until
  history is wired backend-side.
- **V-P2.3 TrustScore TabsList** switched from `flex-wrap` to
  `overflow-x-auto flex-nowrap` with hidden scrollbar.
- **V-P2.4 Amber/yellow "More/Less" toggle color collision** fixed across
  `<ClaimOverlay>` and `<VerificationStatusBar>` —
  `text-muted-foreground hover:text-foreground` now used everywhere (amber
  was sending false "warning" semantics where it just meant "expand").
- **V-P2.5 ExternalLink icon** bumped from `h-2.5 w-2.5` (10 px, illegible)
  to `h-3 w-3` (12 px minimum).
- **V-P2.6 External-reference card body** now fully clickable via a
  wrapping `<a>` (44 × 44 + tap target); inline `ExternalLink` icon stays
  for visual signal.
- **V-P2.7 Inline `<mark>` highlights** gained `aria-label="Claim:
  <status>"` so screen readers can announce the verification band.

*Chat surface (Phase 7):*
- **C-P0.1 Esc-to-stop streaming** — textarea stays focusable during
  streaming (was `disabled`, blocking all keyboard access). Streaming
  state surfaces via `aria-readonly` + a streaming placeholder; Esc binds
  to `onStop`.
- **C-P0.2 Scroll anchoring** — `chat-messages.tsx` now tracks
  `userScrolledUpRef` (true when scrollTop is >100 px above bottom) and
  only auto-scrolls when the user is at the bottom. Force-scroll fires
  when the latest message is a user message (i.e., they just hit Send).
- **C-P1.2 Message timestamps** — relative-time `<time>` element rendered
  beneath each bubble, ticking every 30 s; absolute time in the `title`
  attribute.
- **C-P1.4 Source-attribution + KB-context-indicator merge** —
  `<SourceAttribution>` now takes a `variant: "card" | "badge"` prop;
  `<KBContextIndicator>` is a compat shim forwarding to
  `<SourceAttribution variant="badge">` until consumer sites swap.
- **C-P1.5 Model select** — provider-grouped via `SelectGroup` /
  `SelectLabel` / `SelectSeparator`; unconfigured providers' models are
  disabled with a "Not configured" hint. `configuredProviders` threaded
  from `chat-panel.tsx` → `chat-toolbar.tsx` → `<ModelSelect>`.
- **C-P1.6 ArrowUp recall** — when the textarea is empty and the user
  presses ↑, the last-sent message is restored.
- **C-P2.1 Drag-over overlay** with explicit text labels ("Drop file to
  attach" vs "Drop artifact to inject"). Previously just a ring color
  shift.
- **C-P2.3 Conversation-list search** — `useMemo`-built search index
  replaces the per-keystroke O(N × M) `.some(m => m.content.includes(q))`
  scan.
- **C-P2.6 Private-mode L3/L4 pulse** — one-shot 3-second pulse on
  activation only, then static. (Was persistent infinite pulse.)
- **C-P2.7 ChatDashboard** token-count visibility breakpoint dropped from
  `xl:` (1280 px+) to `lg:` (1024 px+) so laptop users see actual data.

**Wave 3 — Light-mode polish + mixed-bag fixes + motion additions** (hand
edits + Phase 9 motion agent)

*Phase 3 (light-mode):*
- `text-brand-shine` now uses a darker teal-700 → teal-800 gradient in
  light mode (`#0D9488` → `#115E59`) — readable against the pale
  background. Dark mode keeps the original bright shimmer.

*Phase 8 (mixed-bag):*
- **P1.9 Status bar gold border tier-aware** — the gold top divider
  (`border-[rgba(212,175,55,0.22)]`) now appears only for `pro` /
  `enterprise` tiers; community tier uses neutral `border-border`.
  Removes the gold-vs-teal accent collision at the default tier.
- **P1.10 Knowledge filename truncation** — new `displayFilename()`
  helper in `artifact-card.tsx` strips the repetitive
  `memory_(empirical|decision|preference|project|temporal|conversational)_`
  prefix from the displayed filename. Full filename remains in `title=`.
- **S-P1.2 Cost formatter audit** — `essentials-section.tsx` (Today /
  This Month / Balance) and `openrouter-key-field.tsx` now route through
  the canonical `formatCost()` util. Today's spend no longer shows 4
  decimal places on values > $0.01.

*Phase 9 (motion additions, all in the 150–300 ms band):*
- **M-A.1 `<ClaimBadge>` settle fade-in** — `animate-in fade-in
  zoom-in-95 duration-200` keyed on band so state changes (loading →
  verified/refuted) animate cleanly.
- **M-A.2 Streaming caret** — single-blink caret at the tail of the
  active streaming assistant message.
- **M-A.3 Copy-to-clipboard checkmark scale-in** — `animate-in zoom-in-50
  duration-150` on the Check icon when copy succeeds. Applied in
  `<MessageBubble>` + `<OllamaCopyRow>`.
- **M-A.4 TrustScore chip number tween** — 200 ms opacity fade keyed on
  score, so re-verifications animate the new value in.
- **M-A.5 Sidebar active-pane indicator** uses the View Transition API
  (feature-detected) for a sliding crossfade between active panes.
- **M-A.6 Verification step pulse** — exactly one pending step pulses at
  a time (per ui-ux-pro-max guideline #7).
- **M-A.7 Setup Wizard step transition** — replaced the 800 ms blocking
  `setTimeout` with `animate-in fade-in zoom-in-95 duration-300` on
  key-change. Felt faster, no dead time.
- **M-A.8 `<SaveButton>` primitive** — new component at
  `components/ui/save-button.tsx` wraps shadcn `<Button>` with a 1.2 s
  success-state Check zoom-in. Available for adoption by Settings + KB
  save flows.

**Cumulative verification.** 1073 frontend tests + 4036 Python tests
green; tsc + ruff + mypy + lint-imports + drift (0 violations) +
silent-catch + product-story all clean. Snapshot regenerations: 5 across
2 files (motion-class additions on TrustScore chip + VerifiedResponse).

**Anti-pattern audit residual.** 0 P0 visible bugs. 0 active anti-pattern
violations. 13 design-drift allowlist entries remain documented (each
with section-header rationale: runtime geometry, pinned widths,
brand-pinned type sizes, workflow editor chrome).

## v0.92.2 — UI Audit Phase 1 + 2 (2026-05-11) — SUPERSEDED

Quick-win patch following the comprehensive UI/UX audit at
[`tasks/2026-05-11-ui-audit.md`](tasks/2026-05-11-ui-audit.md). Phase 1
clears the visible-bug ledger (every P0 marked "obviously wrong"); Phase 2
closes the accessibility gap on reduced motion.

**P0 fixes — visible bugs**
- **Memories pane crash** — wrap the entire app in `<TooltipProvider>` at root
  (App.tsx) per shadcn rule #39. Memory pane was rendering `<Tooltip>` without
  a local provider and crashing on mount. Audit ref P0.1.
- **Sparkle "✦" bullets in setup wizard** — replaced with `<CheckCircle2>`
  lucide icons. Audit ref P0.2.
- **Lightning "⚡" emoji in status bar** — replaced with `<Zap>` lucide icon.
  Audit ref P0.3.
- **Emoji icons (⚡ 🔬 🔧) on Settings tier cards** — `lib/user-presets.ts`
  now exposes `Icon: LucideIcon` (`Zap` / `FlaskConical` / `Sparkles`)
  instead of `emoji: string`. Audit ref S-P0.1.
- **"View source" disabled affordance lie** on wiki entity-detail —
  permanently-disabled button removed until the route ships, replaced with
  a docblock comment pointing to the audit. Audit ref V-P0.4.
- **`<ClaimBadge>` unverified icon: `CircleDot` → `XCircle`** — `CircleDot`
  reads as "radio button selected" not "no source found". `XCircle`
  completes the verified/partial/unverified semantic ladder (Check / Minus /
  X). Audit ref V-P1.2. Snapshot updated.
- **`VerificationBadge` accuracy formula mismatch** — `message-bubble.tsx`
  was dividing by `total` (includes uncertain/skipped); the status bar
  divides by `verified + refuted`. Two surfaces reported different numbers
  for the same message. Aligned to `verified / (verified + unverified)`.
  Audit ref V-P0.5.
- **Welcome screen "Recent" preview shows first message** — was
  `c.messages[0]?.content?.slice(0, 80)` (the user's opener); now
  `c.messages.at(-1)` (the most recent message). Audit ref C-P2.4.

**Phase 2 — Motion accessibility**
- **`@media (prefers-reduced-motion: reduce)` block** added to
  `src/web/src/index.css`. Collapses all `animation-duration` and
  `transition-duration` to 0.01ms, explicitly disables the three infinite
  decorative loops (`.dark .glow-teal`, `.dark .text-brand-shine`,
  `.scroll-title` marquee), and resets `scroll-behavior` to auto. Single
  highest-leverage accessibility change in the codebase. WCAG 2.1 SC 2.3.3.
- **Dead CSS removed**: `.shimmer-gold` keyframes + class (zero callers)
  and `.float` keyframes + class (zero callers). Pure bundle bloat.

**Verification** — 1044 frontend tests + 4036 Python tests green; ruff +
mypy + lint-imports + drift (0 violations) + silent-catch +
product-story all clean; tsc green.

**Effort** — 45 minutes wall-clock; 9 file edits, 1 snapshot regen, 2
allowlist line bumps.

**Next** — Phases 3-9 of the audit (light-mode polish, `<PaneError>` /
`<SegmentedControl>` / `<TierSelector>` primitives, chat surface
ergonomics, motion ADDITIONS, status bar tier-aware gold) remain queued.

## v0.92.1 — UX Polish + Drift Closeout (2026-05-11)

Same-day follow-up to v0.92.0 closing every remaining open item from the
cohesion-release punch list plus four UX-quality passes the user wanted to
land before the release stabilised.

**D.1 design-drift cleanup → 0 violations.** Added three custom typography
tokens (`--text-label-xxs / -xs / -sm`) to `src/web/src/index.css` plus a
`tailwind-merge` extension teaching `cn()` that these are font-size classes
so they don't collide with text-colour classes. Bulk-migrated 538 instances
of `text-[8/9/10/11px]` across ~80 files to the new tokens. Introduced two
new shadcn-style primitives — `<ProgressBar pct=… size=… fillClassName=…>`
and `<Textarea>` — replacing 11 hand-rolled inline-`style={{ width: … }}`
progress bars and three duplicated `<textarea className="ring-[3px]…">`
blocks in `governance-section.tsx`. The remaining 50 legitimate
runtime-geometry / pinned-width exceptions are documented in
`scripts/design_drift_allowlist.txt` (each entry has a section header
explaining the rationale). The `lint-no-design-drift` CI job now passes
through `--allow-file` and reports `OK — 0 violations`; flip to blocking
once two consecutive `main` runs stay clean.

**Phase 4 UX polish (Linear/Vercel/Stripe-quality pass).**
- **Private Mode** (`chat-toolbar.tsx`): L4 ("Full ephemeral") now opens
  an `AlertDialog` confirmation gate before wiping the session — mis-click
  was previously irreversible. Each level radio item gained a two-line
  layout (label + consequence description, matching the RAG-mode pattern).
  The "Private" badge in the toolbar header now tracks the level colour
  (green / yellow / orange / red) instead of always being amber, and the
  overflow menu's Private item mirrors the per-level colour.
- **Agent Console** (`agent-console.tsx`): the connection-status dot now
  surfaces `connecting` / `retrying` states with a yellow pulse + caption
  (`"Reconnecting (attempt 3/10)…"`); the empty state is a centred
  `<Activity>` icon + prompt instead of a bare muted log line. Cards
  (`agent-cards.tsx`) show a "Completed Ns ago" relative timestamp next to
  the `ok` badge, refreshed every 10s while any card is in the success state.
- **Model Management** (`model-management.tsx`): `PriceChangeRow` renders
  before-and-after costs with directional arrows (red up / emerald down /
  flat); cost labels on `NewModelRow` disambiguate input vs output as
  `in $X · out $Y / 1M tok`; "Last checked" uses relative time with the
  absolute value in a `title=`; the post-check feedback is now a teal
  `<Alert>` instead of muted body text.

**P1 deferred from v0.92.0 — now landed.** Added `HealthInvariants`
interface to `src/web/src/lib/types.ts` and rendered a new
`<InvariantsCard>` in the monitoring pane that surfaces the v0.92 fields
not already shown elsewhere: `healthy_invariants` rollup, NLI model load
state, memory consolidation failures (24h), verification report orphans,
and swallowed-error totals.

**Cross-pane navigation (R.2-link wiring closeout).** Created
`<NavigationProvider>` in `src/web/src/contexts/navigation-context.tsx`
exposing `goTo(pane)` and `composeChat({ text })`. Wired the GraphExplorer's
entity-pill click to deep-link the wiki pane via `?entity=<canonical_id>`,
and "Ask about this community" to seed the chat composer with the
community summary. ChatInput consumes the seed on pane-mount, focuses the
textarea, and leaves the text editable for the user to refine.

**Backend closeouts.**
- `scripts/backfill_entities.py` migrated to enqueue
  `EntityExtractionJob` via the Redis processor queue by default
  (`--in-process` retains the legacy direct-call path for ad-hoc
  diagnostic runs). Progress is now visible in the Processor pane and
  the `processor_*` `/health.invariants` fields.
- New `scripts/lint-product-story.py` drift gate: asserts
  `docs/PRODUCT_STORY.md` exists, has a `> **Last reviewed:** YYYY-MM-DD`
  line ≤ 90 days old, and references all five canonical primitives. 8
  unit tests in `src/mcp/tests/test_lint_product_story.py` cover the
  happy path, every failure mode, and a real-doc sanity check. Job
  wired in CI as `lint / product-story`.

**Dependabot bumps.** `npm overrides` for `next > postcss` in `src/web/`
clears 2 transitive postcss vulns (next was a transitive dep of `geist`);
bumped `vitest 2.x → 4.1` in `packages/sdk/typescript/` to pull in the
vite/esbuild patches. Both manifests now report `found 0 vulnerabilities`.

**Verification.** 1044 frontend tests green (vitest), 4036 Python tests
green (pytest), ruff + mypy + lint-imports clean, drift lint reports
0 violations against the allowlist, silent-catch + product-story gates
pass. Type-check (tsc + mypy) green across both layers.

Key files: `src/web/src/index.css`, `src/web/src/components/ui/{progress-bar,textarea}.tsx`,
`scripts/{design_drift_allowlist.txt,lint-product-story.py}`,
`src/web/src/contexts/navigation-context.tsx`,
`src/web/src/components/monitoring/invariants-card.tsx`,
`src/mcp/scripts/backfill_entities.py`, `src/mcp/tests/test_lint_product_story.py`.

## v0.92.0 — Cohesion Release (2026-05-11)

The "feature collection without a story" → coherent product release. Eight
weeks of phased work closing the cohesion gap surfaced by external + internal
review. Plan driver:
[`tasks/2026-05-10-v0.92-final-plan.md`](tasks/2026-05-10-v0.92-final-plan.md).
Full phase ledger in [`docs/COMPLETED_PHASES.md`](docs/COMPLETED_PHASES.md).

### Five primitives

1. **Verification** — canonical `<VerifiedResponse>` component renders 3
   linguistic bands (verified / partial / unverified) across every chat
   surface; hover-discloses per-claim confidence + provenance.
2. **TrustScore** — `GET /observability/trust-score` returns a 0–100
   system-eval-posture composite of 5 components; status-bar chip + monitoring
   chip + click-through modal with per-component history.
3. **Narrative Loop** — daily brief (06:00) + Monday weekly synthesis enqueued
   through the processor. Brief generation uses the same retrieval +
   verification stack as every other answer.
4. **Wiki** — auto-generated entity pages from the GraphRAG entity layer with
   W.4 contradiction ledger inline; API.3 dispatcher enriches via 8 curated
   public APIs.
5. **Background Processor** — unified Redis-backed queue + worker + chaos
   suite + 6 concrete job types (entity_extraction, brief_generation,
   weekly_synthesis, wiki_refresh, ingest_recovery, hype_indexing). New
   `/health.invariants` metrics: `processor_jobs_completed_24h`,
   `processor_cost_usd_7d`, `processor_throttled_ticks`. CPU-aware
   throttling; cost-projected hybrid mode.

### Supporting phases

- **API.1–2** — 8 curated public-API adapters (Wikipedia, Wikidata, OpenLibrary,
  Stack Exchange, arXiv, GitHub, PyPI+npm, OSM) registered at `/external-apis`
  with per-adapter Redis-backed enable state + Settings UI section (gated by
  `<AdvancedMode>`).
- **API.3** — `WikiRefreshJob` enrichment dispatcher; entity-type heuristic
  selects adapters; external references render in a clearly-labeled section
  on entity pages (visually distinct from internal source artifacts).
- **API.4** — `POST /sdk/v1/ingest/external` generic ingest endpoint with
  dotted-path field mapping. Documented adapters: Readwise, Pocket, Instapaper,
  Raindrop, Telegram-bot (see [`docs/INTEGRATION_GUIDE.md`](docs/INTEGRATION_GUIDE.md)).
- **R.1** — thumbs feedback loop persists `(:User)-[:RATED]->(:Claim)` edges
  feeding TrustScore component #6 + operator `/observability/claim-accuracy/{domain}`.
- **R.2** — interactive Leiden community explorer (`<GraphExplorer>` pane);
  cached community summaries from Phase-4b surface via
  `GET /observability/communities`.
- **R.3** — HyPE at index time stored in parallel ChromaDB collection
  (`{base}_hype`); off-by-default behind `RETRIEVAL_HYPE_ENABLED`; documented
  flip protocol in [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md) (≥+0.02
  NDCG@10 sustained across 2 consecutive full-corpus runs).
- **O.1** — cross-store ingest atomicity: two-phase write
  (`cerid_state=pending → committed`) + 60s `IngestRecoveryJob` heartbeat +
  retrieval-gate filter at the 3 actual Chroma chokepoints. Preservation
  gate I19.
- **O.2** — memory consolidation preservation gate I20 +
  `memory_consolidation_failures_last_24h` invariant via core→app callback
  pattern (preserves layer boundary).
- **O.3** — preservation harness "skip with prejudice": silent skips become
  warn-and-record-in-junit; new `lint-no-silent-preservation-skips` CI job
  fails main-branch builds when any preservation test would have skipped.
- **U.1** — wizard "Try a sample pack" tab inside `first-document-step.tsx`
  (no new wizard step); 4 featured packs from the v1.0.1 knowledge-pack
  catalog.
- **U.2** — `<AdvancedMode>` wrapper + canonical "Show advanced" settings
  toggle; 3 operator-tier settings tabs gated; tab-reset effect prevents
  blank-content state when toggling.
- **U.3** — `@cerid/widget` vanilla-HTMLElement web component
  (**6.32 KB gzipped CDN**, no framework runtime); two build modes
  (library ESM/CJS + IIFE CDN); inline lucide-equivalent SVGs.
- **U.4** — settings consolidation: 2 duplicate controls merged (Data
  Sources → System tab; Model Updates → Pipeline tab).
- **D.1** — design-tokens drift gate (`scripts/lint-no-design-drift.py`); 591
  violations cataloged in `tasks/2026-05-10-D1-design-drift-punch-list.md`
  with 7-batch remediation plan; soft-warn CI job per the
  `sdk-openapi-drift` ladder.
- **D.2** — 4-state matrix pass per pane (idle / loading / empty / error)
  using shadcn `Skeleton` + `Alert`; gap-filled `monitoring-pane`,
  `memories-pane`, `knowledge-pane`.
- **D.3** — axe-core a11y CI job (42 axe-clean tests); 6 a11y violations
  fixed across `trust-score-chip` Skeleton wrapper, `memories-pane` button
  labels, `knowledge-pane` SelectTrigger labels.

### Foundational artifacts

- [`docs/PRODUCT_STORY.md`](docs/PRODUCT_STORY.md) — canonical product narrative
- [`docs/BACKGROUND_JOBS.md`](docs/BACKGROUND_JOBS.md) — operator one-pager
- [`docs/EXTRACTION_PLAN.md`](docs/EXTRACTION_PLAN.md) — worker-extraction ADR stub
- `CLAUDE.md` Mechanical overrides 6 + 7 — token budgets, phase checkpoints
- Preservation harness: 8 foundation invariants + 3 v0.92 gates (I17 / I19 / I20)

### Aggregate state at this release

- 1,711+ v0.92 tests pass (560+ backend unit + chaos + 940+ frontend + 69 widget)
- All blocking CI gates green: ruff, mypy, lint-imports, router-registry-drift,
  sdk-openapi-drift, lint-silent-catch, lint-no-design-drift, lint-no-legacy-neo4j,
  env-example-drift, sync-manifest-drift, lock-sync
- New soft-warn gates: `lint-no-design-drift`, `frontend / a11y (axe-core)`,
  `lint-no-silent-preservation-skips` (per the standard ladder; promote after
  2 consecutive green main runs)
- `core → app` boundary held throughout
- Build: 152 KB gzipped main frontend bundle + 6.32 KB widget CDN bundle

## v0.91.1 — Infrastructure migrations + GraphRAG retrieval (2026-05-09)

Chromadb 0.5 → 1.x (client + server) + Neo4j 5.26 → 2026.04.0 calver
(server + APOC + GDS plugin) + the full Workstream E Phase 4 GraphRAG
arc (entity layer + community layer + auto query routing) all landed
end-to-end on top of v0.91.0. The shared compose stack on the
maintainer machine has been migrated lossless (chromadb: 13,264
chunks; Neo4j: 11,672 artifacts + 12,840 nodes + 46,622 rels), and
both internal and public CIs are green.

### What's already on `main`
- **Phase 1 (`5040e96`)** — `core/retrieval/semantic_cache.py` rewritten
  atop a chromadb collection. Drops the chroma-hnswlib transitive,
  retires the 16-byte `_HNSW_MAGIC` dim self-heal protocol, adds lazy
  orphan eviction. Public API preserved; layering preserved (no
  chromadb import in `core/`).
- **Phase 2a (`b169c91`)** — `OnnxEmbeddingFunction` gains the chromadb
  1.x EF contract: `name()` / `get_config()` / `build_from_config()`.
  No-op on 0.5; live the moment the pin lifts.
- **Phase 2b (`2d5e5df`)** — `chromadb>=1,<2` in `requirements.txt` +
  `requirements.lock` regenerated; `_startup_compat.py` deleted (1.x
  retired the chromadb→posthog telemetry path so the shim is dead);
  heartbeat probe paths in `app/main.py` + `app/routers/setup.py`
  flipped `/api/v1/heartbeat` → `/api/v2/heartbeat`;
  `scripts/reembed_collection.py` updated for `IncludeEnum` retirement.
- **Phase 2c (`9ce7c54`)** — `app/sync/import_.py` migrated to the v2
  REST path scheme `/api/v2/tenants/{tenant}/databases/{db}/collections/...`
  via a single `_v2_collections_base()` helper.
- **Cleanup (`d582cfb`)** — dead `SEMANTIC_CACHE_HNSW_EF` env var removed.

### Phase 3 — committed locally, **not pushed**

`docker-compose.yml` chromadb block: image
`chromadb/chroma:0.5.23` → `chromadb/chroma:1.5.9`; volume mount target
`:/chroma/chroma` → `:/data` (1.x persistence path moved); healthcheck
`/api/v1/heartbeat` → `/api/v2/heartbeat`.

**WARNING — destructive on first boot.** chromadb 1.x runs
`migration_mode: "apply"` against the host data dir; the 0.5-era sqlite
+ segment files are transformed in place. **One-way.** Rollback
requires a tarball snapshot restore.

**Operator runbook before activating** (full version in
`docs/DEPENDENCY_UPGRADES.md` § ChromaDB Phase 3):

1. `docker compose stop chromadb`
2. `./scripts/backup-kb.sh` (snapshot all three volumes)
3. `git pull` (after maintainer pushes the Phase 3 commit)
4. `docker compose pull chromadb && docker compose up -d chromadb`
5. Verify `/api/v2/heartbeat` 200 + collections survived (preservation
   harness)

### Neo4j Phase 2 — server bump 5.26 → 2026.04.0 + GDS plugin

`docker-compose.yml` neo4j block: image `neo4j:5.26.21-community` →
`neo4j:2026.04.0-community`; plugins
`["apoc"]` → `["apoc","graph-data-science"]`; heap `1G → 4G` (GDS in-memory
projections); container memory `4G → 8G`; healthcheck `start_period`
`30s → 60s` (longer cold-boot with GDS catalog registration).

**Pre-edit verification (empirical, against `neo4j:2026.04.0-community`):**
- Driver `neo4j>=6,<7` (currently 6.1.0) speaks the calver Bolt
  protocol cleanly: auth probe `RETURN 1` works; `CREATE CONSTRAINT
  ... IF NOT EXISTS FOR ... REQUIRE` works; `MERGE` + `MATCH` +
  relationship writes work.
- `CALL gds.version()` returns `2026.04.0` (synced version line).
- `apoc.version()` returns `2026.04.0`.
- Plugin name correction: `gds` short form is rejected by calver
  (accepted: `apoc`, `apoc-extended`, `bloom`, `fleet-management`,
  `genai`, `graph-data-science`). Earlier migration plan had the
  short form; corrected to full name.
- Direct in-place upgrade 5.26 → 2026.x is supported (Neo4j docs;
  no intermediate version, no store-format change between 4.4 and
  2026.x); existing databases default to Cypher 5 on first 2026.x
  boot.

**Operator runbook before activation** (also in
`docs/DEPENDENCY_UPGRADES.md` § Neo4j Phase 2):

1. `docker compose stop cerid-web mcp-server neo4j`
2. `./scripts/backup-kb.sh` (snapshot all three volumes)
3. `git pull` (after maintainer pushes the Phase 2 commit)
4. `docker compose pull neo4j`
5. `docker compose up -d neo4j` — wait for healthy (cold boot ~45–60s
   with GDS plugin install on first boot)
6. `docker compose up -d mcp-server cerid-web`
7. Verify `curl -s http://127.0.0.1:8888/health` shows
   `services.neo4j == "connected"` + `healthy_invariants == true`
8. Verify GDS available:
   `cypher-shell` → `CALL gds.version()` returns `2026.04.0`

Rollback: stop neo4j, restore the snapshot tarball over
`./stacks/infrastructure/data/neo4j`, revert the docker-compose neo4j
block to the 5.26.21 image + `["apoc"]` plugins + 1G heap + 4G memory,
restart.

### Workstream E — GraphRAG retrieval (Phases 4a + 4b end-to-end, 2026-05-09)

Builds the entity + community layers on top of the upgraded
chromadb 1.x + Neo4j calver stack. All sub-phases landed on `main`;
the corpus backfill (operator-invocable) and apscheduler wiring of
`refresh_communities.py` are the only remaining touch-points before
this arc is fully populated on the live dataset.

**Phase 4a — entity layer + local mode:**

| Sub-phase | Commit | What |
|---|---|---|
| 4a.0 | `e085d98` | `neo4j-graphrag>=1.16,<2` dep |
| 4a.1 | `e085d98` | spike: ship custom `ChromaNeo4jRetriever` shim |
| 4a.2 | `fbe6eed` | `(:Entity)` schema (`canonical_id` UNIQUE + `name`/`entity_type` indexes) |
| 4a.3 | `89ec055` | `core/agents/entity_extraction.py` + `app/db/neo4j/entity.py` (LLM NER, type vocab, canonical_id, idempotent UPSERT) |
| 4a.4 | `4dccaed` | `scripts/backfill_entities.py` — resumable, checkpointed; 5-art pilot validated |
| 4a.5 | `5dc7445` | `core/retrieval/graphrag_retriever.py` — `ChromaNeo4jRetriever` shim subclassing `ExternalRetriever` |
| 4a.6 | `56d2e21` | `RETRIEVAL_MODE` switch + step-6 `graph_expand_results_via_entities` + `embed_query` polymorphism fix (chromadb 1.x list-vs-string protocol) |
| 4a.7 | `b676ac3` | `tests/eval/graphrag_local_benchmark.py` — entity-grounded pseudo-gold harness |

Layering preserved throughout: `core/` keeps zero `chromadb` import
(the chroma collection is injected via `_CacheBackend`-style protocol
into `ChromaNeo4jRetriever`).

**Phase 4b — community layer + global mode:**

| Sub-phase | Commit | What |
|---|---|---|
| 4b.0 | `97a5b12` | `graphdatascience>=1.21,<2` Python client |
| 4b.1 | `5a3e658` | `app/db/neo4j/community_detection.py` — Leiden over Entity CO_MENTIONED graph (UNDIRECTED projection, all hierarchical levels); writes `(:Community)` + `IN_COMMUNITY` edges |
| 4b.2 | `0531040` | `app/db/neo4j/community_summaries.py` — top-K-by-degree entities + 1 representative chunk per entity → `call_internal_llm` (Ollama Haiku) → cached on `Community.summary` |
| 4b.3 | `419ac3e` | `core/agents/query_router.py` — heuristic v1 (`>15 words ∧ no quoted spans ∧ no proper nouns → global`) |
| 4b.4 | `4eff566` | `RETRIEVAL_MODE=auto` + `graph_expand_results_via_communities` in step-6; `scripts/refresh_communities.py` composes Leiden + summaries for nightly invocation |
| 4b.5 | `6e0905a` | `tests/eval/graphrag_global_benchmark.py` + full unit-suite green (3065 tests) |

**Live verification** (against running stack: chromadb 1.5.9 + neo4j
2026.04.0 + GDS 2026.04.0; 26-artifact pilot dataset):

- Entity extraction: 26 artifacts → 143 entities, 7 distinct types
- Local-mode expansion: seed → 5 related artifacts via 4 shared entities
- Leiden: 24 communities at level 0, **modularity 0.79**, semantically coherent (financial-crisis / oncology / food-science / embryology clusters)
- Global-mode expansion: seed → community summary covering its theme

**CI hotfix** (`2d33001`): typecheck (dict-set annotation in
`community_detection`), strict silent-catch in projection cleanup
(`log_swallowed_error` instead of `pass`), DUO138 ReDoS false-positive
on `_PROPER_NOUN_RE` (documented + `# noqa: DUO138`).

**Corpus purge + post-tag operator activation (2026-05-09):**

- Discovered ~75% of the 11,755 artifact mass was bulk medical literature
  (5,183 PubMed-like + 3,591 MED-NNNN); largely irrelevant for the
  documented "general use + personal-assistant knowledge context" RAG
  purpose. The memory-artifact subspace was further dominated by 2,846
  `memory_empirical_*` records — diagnostic test outputs from
  `cerid-trading-agent` that landed in cerid-ai's KB by accident.
- Authorised purge: dropped the 2,846 trading-agent diagnostic artifacts
  from chromadb `domain_conversations` + Neo4j (cascade DETACH DELETE
  pulled 16,524 MENTIONS edges; orphan-Entity cleanup dropped 1,337
  nodes + 6 CO_MENTIONED edges). Final corpus: **8,910 artifacts**.
  Snapshot at `backups/2026-05-09_18-09-57` if rollback needed.
- Tiered backfill produced 382 artifacts with entity mentions across
  T1 (personal/work, 42 art), T2 (real memories, 7 art), T3 (top-500
  by quality from the MED bulk, 333 art). **2,536 entities, 3,367
  MENTIONS edges, 24,322 CO_MENTIONED edges.**
- Leiden refresh: **410 / 245 / 230 / 228 communities** across levels
  0..3 (1,093 total), 28.2s. **405 LLM-summarised** (rest below size
  threshold).

**GraphRAG eval lift on the populated graph:**

| Metric | baseline | local_graphrag | lift |
|---|---|---|---|
| NDCG@10 | 0.011 | 0.107 | **+875%** |
| Recall@10 | 0.005 | 0.034 | **+653%** |
| Precision@10 | 0.010 | 0.092 | **+822%** |
| Queries scoring nonzero | 1/10 | 3/10 | 3x coverage |

Eval is pseudo-gold (entity-mention sets stand in for human labels),
so absolute scores are bounded by per-query gold sets that exceed
top-k. The meaningful signal — the **~10x lift of local_graphrag over
baseline across all metrics** — confirms entity-neighborhood
expansion does real work on the populated graph.

Global-mode benchmark: 4/5 thematic queries surfaced ≥1 community
summary in `global_graphrag` mode vs zero in `local_graphrag` —
the routing-shape distinction holds as designed.

## v0.91.0 — Workstream A close-out + C + D Phase 1 + E Phase 2 (2026-05-03 → 2026-05-08)

Cross-project SLO hardening (with `benchmark-slo` now PR-blocking) +
Pro-tier checkout end-to-end + Python 3.12 runtime bump + retrieval-quality
work + the systemic close-out of the trading-agent interface ledger.

### Workstream A close-out gate flipped (2026-05-08)

- **`benchmark-slo` promoted to PR-blocking.** After 4 consecutive green
  main runs (matching the `sdk-openapi-drift` 2026-04-21 precedent),
  `continue-on-error: true` removed and `benchmark-slo` added to the
  `docker` job's `needs[]` list. Real-OpenRouter latency drift now
  blocks merges, complementing the deterministic budget-plumbing tests
  in `test_memory.py` / `test_memory_consolidation.py` that gate the
  per-stage `asyncio.wait_for` wrappers.

### Workstream D Phase 1 — Python 3.12 runtime (2026-05-08)

- **Python 3.11 → 3.12.** Dockerfile (builder + runtime stages +
  site-packages path), `pyproject.toml` (`requires-python`, ruff
  `target-version`, mypy `python_version`), and 14 `setup-python`
  blocks across `ci.yml` all converged on 3.12. The lock-sync job
  was already running in `python:3.12-slim`; CI / Dockerfile / lock
  now all match. Lock regenerated; no Python-side code changes
  required (no `match` adoption needed for the bump itself).
- **Neo4j Python driver pin tightened to `>=6,<7`.** Audit-verified
  clean: all sessions use `with`, `Result.summary()` migrated to
  `consume()`, no deprecated `read_transaction`/`write_transaction`,
  no `Transaction.sync`. Driver 7.x doesn't exist on PyPI yet
  (latest is 6.2.0); the next phase of the Neo4j upgrade is
  server-side (5.26 → 7.x + GDS plugin), tracked separately due to
  the one-way data-volume migration.

### Deferred (multi-day, separate sessions)

- **chromadb 0.5 → 1.x.** `semantic_cache.py` rewrite-class (~520 LOC),
  `chroma-hnswlib` drops, REST v1→v2 path changes, one-way data-volume
  migration. 4-phase plan documented in `tasks/todo.md`.
- **Neo4j server 5 → 7 + GDS plugin.** Memory budget bump required for
  GDS in-memory projections (heap 1G → 4G, container 4G → 8G).
- **E.4a/4b GraphRAG.** Gated on Neo4j 7 + GDS. Spike resolved:
  `ChromaNeo4jRetriever` subclass (~150 LOC) keeps vectors in Chroma
  while entity graph lives in Neo4j 7 — preserves the
  vectors-in-Chroma / graph-in-Neo4j architectural split documented in
  `docs/COMPETITIVE_ANALYSIS.md`.
- **ESLint 9 → 10.** Original blocker (`eslint-plugin-react-hooks`)
  cleared; new blocker is `eslint-plugin-jsx-a11y@6.10.2` (peer caps
  at ESLint 9, no release in ~18 months). ~1-hour task once jsx-a11y
  ships.

### Cerid-AI interface contracts (closes the trading-agent ledger)

Five systemic invariants land here, each addressing a class of problem
the trading-agent had to work around client-side. Every change is
schema-enforced (Pydantic + OpenAPI), test-gated, and back-compatible.

- **D — Object-envelope contract on `/agent/memory/recall`.** Bare
  `[]` returns broke naive `body.get(...)` parsers and inflated
  consumer error rates. Now returns `{memories, total}` via a typed
  `MemoryRecallResponse` model with `response_model=` enforcement on
  the route.
- **E — `min_length=1` on required-but-empty-trapped identifiers.**
  Empty `conversation_id` no longer reaches the handler's runtime 422
  branch — Pydantic rejects up-front, the constraint surfaces in the
  OpenAPI spec, and the `sdk-openapi-drift` gate keeps it stable.
- **C — `slo_budget_ms` on `/sdk/v1/llm/complete`.** Smart-router
  filters tiers by their empirical p95 latency profile; if no tier
  fits the budget, the handler returns `503` with a `Retry-After`
  header carrying the floor p95. Never silently downgrades — quality
  drops would be invisible to the caller. Response now carries
  `tier_p95_ms` so callers can tune adaptive client-side timeouts.
- **B — `mode=fast | thorough` on `/agent/hallucination`.** Fast mode
  runs claim extraction only and returns claims marked
  `status='uncertain'` with `nli_skipped=true` — same envelope, no
  cross-model NLI cost. Thorough mode (default) preserves existing
  behaviour. Trading-agent's client-side `asyncio.wait_for(2.0)` wrap
  becomes a server-side contract.
- **A — async-by-default `memory_extract` with sync escape hatch.**
  When `MEMORY_QUEUE_MODE=async`, `POST /sdk/v1/memory/extract`
  returns `202` + `job_id` + `Location` header; callers poll
  `GET /sdk/v1/memory/extract/jobs/{job_id}` for the result.
  `?wait=true` forces sync for callers that need the result inline.
  Lifts the extract→consolidate→store pipeline off the request slot
  entirely — closes the residual 1.0% timeout cluster the per-stage
  budgets (Phase 1.2) couldn't reach.

The async/202 pattern shipped here is the canonical answer for any
future endpoint whose response is "acceptance, not result." A new
`cerid-memory` queue runs alongside `cerid-ingest`; the same worker
process drains both, and each queue has an independent
`*_QUEUE_MODE` opt-in flag.

### Pro tier checkout (Workstream C)

- **Stripe Checkout end-to-end shipped.** The Pro Settings pane's
  upgrade button now opens a Stripe-hosted Checkout URL in a new
  tab; manual license-key entry remains as the offline-activation
  fallback.
- **Webhook coverage expanded** to handle the full subscription
  lifecycle. The previously-unhandled `customer.subscription.updated`
  event now deactivates the license on `past_due` / `unpaid` /
  `canceled` / `incomplete_expired` statuses; `active` / `trialing` /
  `paused` no-op (paused is a customer-initiated vacation hold —
  entitlement is preserved during the pause window).
- **Comprehensive unit-test coverage** added for the billing router
  (every endpoint and every webhook branch, deterministic, < 1 s,
  no live Stripe required).

### Cross-project SLOs (Workstream A)

- **Phase 1.2 — `/sdk/v1/memory/extract` per-stage budgets.** Three
  unbounded LLM call sites (`extract_memories`, `_llm_classify`,
  `resolve_memory_conflict`) now wrapped in `asyncio.wait_for` with
  empirically sized budgets (12s on the load-bearing extract, 8s on
  the consolidation/conflict siblings) and `log_swallowed_error` on
  the timeout branch. Replaces the httpx 20s default that was
  absorbing the 5.7% long tail observed in trading-agent's 20.4h soak.
  Removes `xfail(strict=True)` from the SLO test
  `test_memory_extract_under_10s` so the budget is now a hard CI gate.
- **Phase 1.3 — `/observability/restarts` endpoint + 10s healthcheck
  timeout.** New endpoint exposes process start time, uptime, and a
  Redis-backed monotonic restart counter so trading-agent and other
  dependents can detect MCP boots in one call. Healthcheck timeout
  bumped 5s → 10s so a slow `/sdk/v1/*` response under peak load
  can't false-positive into a restart.
- **Phase 1 close-out gate — split-test design.** Replace the original
  "promote `benchmark-slo` to PR-blocking after 7 consecutive green
  main runs" plan with a two-test approach:
  * **Deterministic budget-plumbing tests** in `test_memory.py` /
    `test_memory_consolidation.py` / `test_pipeline_enhancements.py`
    monkey-patch the per-stage budget down to 50 ms and assert the
    `asyncio.wait_for` actually fires + the fallback path runs +
    `log_swallowed_error` is called. Run inside the default `test`
    job — already PR-blocking, so budget regressions are caught on
    every PR.
  * **Live `benchmark-slo` job** soft-promoted onto the PR pipeline
    with `continue-on-error: true`. Surfaces real-OpenRouter latency
    drift via job-summary + JSON artifact without gating merges.
    Promote to blocking by removing `continue-on-error` and adding
    to the `docker` job's `needs[]` once 4 consecutive green main
    runs accumulate (matching the `sdk-openapi-drift` precedent).

### Retrieval quality

- **Phase 2b — layout-aware parsing flipped to default ON.** Clean win
  on every dimension against the live eval-corpus: `+0.05 MRR`,
  `+0.024 NDCG@10`, `+0.088 precision@5`, latency *improved* 5–14%
  across percentiles. Revertable via `ENABLE_LAYOUT_AWARE_PARSING=false`.
  The new floor lives in `tests/eval/baselines/retrieval.json`.
- **Phase 2a — keyword-coverage scoring family.** Adds the
  exploratory-suite scoring mode that the dual-gate decision (Phase 1.2
  audit) reserved for the 5 keyword-only public datasets.
- **Phase 2b.1 — BEIR seed plumbing.** SciFact (300 queries) and
  NFCorpus (323 queries) gold judgments captured against cerid's
  `relevant_paths` schema. BEIR cache defaults to `/tmp/` (the
  eval-corpus mount is read-only).
- **Phase 2c — nightly exploratory eval workflow.**
  `.github/workflows/eval-exploratory.yml` runs daily at 07:30 UTC,
  boots the stack, seeds the synthetic corpus, runs `benchmark_suite`
  across the synthetic + 5 keyword-only public datasets, and posts a
  job summary + uploads the JSON report. `workflow_dispatch` exposes
  a `seed_beir` toggle for the ~2.5h BEIR pass. Drift is signal, not
  a gate — PR merges still gate on `benchmark-slo` and `ragas-eval`.
- **Phase 3a (RRF default) and Phase 3b (contextual chunks default)
  tested and reverted.** RRF regressed every metric on chunk-level
  corpora; contextual lifted precision@5 only while blowing the
  latency budget by +59% p95. Both remain opt-in via env. Findings
  ledgered in [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md).

### Surface

- **`/sdk/v1/llm/complete`** — smart-routed LLM completion endpoint
  that fans out to OpenRouter / Ollama via the existing `llm_client`
  routing. OpenAPI baseline regenerated.

### Reliability

- **Asyncio test migration.** 9 test files
  (`test_foundation`, `test_pipeline_enhancements`, `test_query_agent`,
  `test_ingestion`, `test_async_ingestion`, plus four orchestrator /
  budget / tools / workflows files) moved off the deprecated
  `asyncio.get_event_loop().run_until_complete(...)` pattern onto
  `asyncio.run(...)`. Clears the 30-test `RuntimeError: There is no
  current event loop` cluster on Python 3.12.
- **`requirements.lock` regenerated** against `python:3.12-slim` —
  `lock-sync` CI gate back to green.
- **Langfuse compose fix.** `HOSTNAME=0.0.0.0` so Next.js standalone
  binds all interfaces, plus `127.0.0.1` (not `localhost`) in the
  healthcheck so busybox `wget` doesn't get refused over IPv6. Both
  root causes documented inline in `stacks/langfuse/docker-compose.yml`.
- **Trivy ignore additions.** `CVE-2026-4878` (libcap TOCTOU race —
  not reachable in single-user container) and `CVE-2026-33845`
  (GnuTLS DTLS path — we use TLS only via httpx). Both with
  re-evaluation dates.

### Sync hygiene

- **`scripts/sync-repos.py` public-repo path resolver.** Walks
  `CERID_PUBLIC_REPO` env → sibling `cerid-ai` (canonical) → sibling
  `cerid-ai-public` (legacy alias). No more "public repo not found"
  on the canonical layout.
- **`.sync-manifest.yaml`** — removed a stale `internal_only` entry
  whose target file no longer exists on disk; closes the
  ``WARN  Missing internal-only file`` noise on every
  ``sync-repos validate``.
- **Untracked `packages/sdk/typescript/dist/`** build output. Added
  to `.gitignore`. Regenerated by `npm run build` when needed.

## v0.90.0 — 2026-04-22

Cerid 0.90 hardens the reliability story while keeping the install
path one command.

### What's new

- **First-run setup wizard.** Guided eight-step path (Welcome → Keys →
  Storage → Ollama → Apply → Health → Try → Mode) replaces the
  copy-edit-restart loop. System check probes Docker, configured keys,
  and Ollama before you commit; hardware auto-detection sizes the
  default mode for your machine.
- **Verification pipeline hardens to one auto-persisting call.**
  `/agent/hallucination` now persists by default — no second
  `/verification/save` round-trip. Streaming verification emits a
  `persisted:{success}` SSE event so the frontend can trust the result.
- **Preservation harness — every release gates on real-stack tests.**
  35 integration tests boot the full docker-compose stack and assert
  end-to-end capability before a merge to main. The `preservation`
  CI job is blocking from this release forward.
- **Silent-error visibility.** Every broad-catch error path now flows
  through `log_swallowed_error()` and surfaces at
  `/health.invariants.swallowed_errors_last_hour`. No more hidden
  degradation.
- **SDK v1 with drift-guarded OpenAPI surface.** Twelve endpoints under
  `/sdk/v1/` plus a committed `docs/openapi-sdk-v1.json` baseline; CI
  fails on accidental shape changes. See [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md).
- **Plugin system — five plugin types.** `parser`, `agent`, `tool`,
  `connector`, `sync`. See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md).
- **Repo structure cleanup.** Re-export bridges retired
  (`src/mcp/services/` and `src/mcp/agents/` removed; `src/mcp/utils/`
  is implementation-only). `import-linter` enforces the layer
  contract — `core/` never imports `app/`.

### Reliability fixes from real-world testing

- Chat key reload: the setup wizard's key change now applies without
  a container restart (was being captured at module-import time).
- Env-file persistence: wizard writes are anchored to a dedicated
  `CERID_ENV_FILE` mountpoint (was writing to an orphan path inside
  the container).
- Default secrets in `.env.example`: `OPENROUTER_API_KEY` is now
  declared and `REDIS_PASSWORD` ships with a non-empty default so
  first `docker compose up` works.
- Fixed an event-loop poisoning bug in the external-verification
  client when called from sync ingestion code.

For architecture, contributor conventions, and the preservation
harness, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/CONVENTIONS.md`](docs/CONVENTIONS.md), and
[`docs/PRESERVATION.md`](docs/PRESERVATION.md).

## v0.84.0 — Reliability Remediation (2026-04-17 → 2026-04-18)

Audit-driven reliability, data-wiring, UX, and LLM-integration fixes across 32 commits. Every P0/P1 concern from the 2026-04-17 live beta audit addressed. Full plan at [`tasks/2026-04-17-reliability-remediation-plan.md`](tasks/2026-04-17-reliability-remediation-plan.md).

### Correctness (Wave 0)
- **`QueryEnvelope` single-writer** — unified `/agent/query` response shape. External results are now always mirrored into `sources` and `source_breakdown.external`; the degraded-budget path no longer drops them.
- **VerificationReport edges + backfill** — writer now always creates `[:EXTRACTED_FROM]` and `[:VERIFIED]`; stores `source_urls` / `verification_methods` on the node for external-verified claims. One-shot `m0001` migration backfills pre-existing reports.
- **Frontend triple-fire killed** — one chat turn used to spawn 3 identical `/agent/query` POSTs. `useChatSend` now skips the redundant refetch when TanStack cache is warm.
- **`DegradedBanner`** — ungrounded answers surface an amber banner with the backend's `degraded_reason`.
- **External source attribution** — `source_breakdown.external` merges into the assistant's "Sources used" pane.

### Reliability (Wave 1)
- **Retrieval budget + CB tuning** — external data-source circuit breakers relaxed from `failure_threshold=1, recovery=120s` to `3 / 30s`. Router-level CRAG gate suppresses external fan-out when top KB relevance ≥ `RETRIEVAL_QUALITY_THRESHOLD`.
- **Cancellation-safe SSE** — `/chat/stream` polls `request.is_disconnected()` and catches `CancelledError` / `GeneratorExit` to close upstream OpenRouter sockets on client abort. O(chunks²) usage parse short-circuited.
- **Partitioned concurrency pools** — `KB` / `CHAT` / `HEALTH` replace the process-wide `_QUERY_SEMAPHORE(2)`. `/health` polling no longer serializes behind chat turns. Queue depth visible at `/observability/queue-depth`.
- **Frontend abort cleanup** — `useChat` aborts on unmount. `queryKBOrchestrated` accepts an `AbortSignal` and threads TanStack's `signal` through.
- **Graceful 429** — rate-limit middleware returns `429` with `Retry-After` header and JSON body `retry_after` instead of dropping connections under burst load.

### Trust (Wave 2)
- **Claim cache schema v2** — keyed on `(claim, model, tier, response_context)`. No more stale verdicts across model swaps or pronoun-resolved claim collisions.
- **Cited-URL verification** — claims with `source_urls` fetch the page and NLI-entail before considering web search. Fabricated citations no longer get confirmed from unrelated search hits.
- **Stream-abort claim finalizer** — pending claim cards flip to `uncertain` on verify-stream close; no more forever-spinning popovers.
- **Startup invariants in `/health`** — collection dim checks, `verification_report_orphans` count, NLI load status. `/health` returns 503 on critical invariant violation.
- **`X-Cache` header + `cached: true` body** — cache hits observable from the client.
- **Version SSOT** — `/`, `/health`, and FastAPI `app.version` all read from `pyproject.toml` via `core/utils/version.py`. `/api/v1/*` dual mount retired.
- **Smart-router scored classification** — replaces first-keyword-match with weighted signals. `cost_sensitivity` now plumbs from `/agent/query` through `route()` and `call_llm`. All registered model IDs carry the `openrouter/` prefix.

### Hygiene (Wave 3)
- Favicon / apple-touch-icon / viewport zoom-lock + conversation-search a11y labels + rapid-Enter race guard.
- Rate-limit now covers `/setup/*`, `/admin/*` GETs, `/observability/*` GETs.
- **Semantic-cache dim self-heal** — stored HNSW blobs carry a magic+dim header; mismatch on load deletes the blob and cold-starts.
- **Dropbox `EDEADLK` retry** — 3-attempt exponential backoff; structured final-failure warning surfaced to the GUI.
- **Shared `httpx.AsyncClient` for OpenRouter credits** — module-level client + lazy getter eliminates per-poll socket churn.
- **Cross-machine settings/conversations reconciliation** — `updatedAt`-based version vector; drift now resolves on the next load.
- **Graceful reranker fallback** — ONNX cross-encoder failure returns results in original order with `reranker_status: "onnx_failed_no_fallback"` instead of crashing.

### Bifrost retirement (audit C-4)
- Bifrost **fully retired** — no container, no helper module, no URL. `utils/bifrost.py`, `core/utils/bifrost.py`, `stacks/bifrost/` deleted.
- Three pipeline callers migrated to `core.utils.llm_client.call_llm` (`utils/metadata.py` topic extraction, `core/utils/contextual.py` chunk summaries, `core/agents/maintenance.py` health probe).
- `USE_BIFROST` / `CERID_USE_BIFROST` env vars removed. `BIFROST_TIMEOUT` kept as a legacy name for a generic LLM timeout.
- `bifrost-*` circuit-breaker names preserved as historical identifiers for call-site categories (rerank / claims / verify / synopsis / memory / compress / decompose).
- `call_llm` / `call_llm_raw` now raise `RuntimeError` when `OPENROUTER_API_KEY` is unset — no silent re-route.
- nginx `/api/bifrost/` proxy removed.

### Testing + CI
- **Smoke harness** (`src/mcp/tests/load/smoke.py` + `make smoke`) — 8 scenarios covering `/health` concurrency, response-shape invariants, cache hit-rate, 429 graceful behaviour, SSE cancel, HOL blocking, CB flap.
- **4 previously-skipped tests restored** (+5 active / -5 skipped): SSE generator `CancelledError` path, reranker graceful-fallback contract, 3 agent-query budget-fixture tests.
- **CRAG inner-gate regression test** scans `query_agent.py` source to prevent re-introduction of the duplicate gate.
- **+16 new backend tests** across VerificationReport persistence, dim-validation, envelope shape-invariants, SSE cancellation, concurrency pools, claim cache key, cited-URL verification, startup invariants, metrics middleware, rate limiting, settings-sync retry, reranker fallback.
- **CI hygiene** — lint / typecheck / tsc / lockfile blockers cleared. Pinned `ruff==0.15.4` + `pip-tools==7.5.3` to match CI exactly.

### Deferred
- **Task 18 — chat-messages virtualization.** First attempt broke 46 testing-library measurement-dependent tests under jsdom. Needs a `@tanstack/react-virtual` approach that doesn't interfere with `measureElement` in jsdom.

### Post-deploy actions
- Run `python -m scripts.run_migrations` (m0001) to backfill existing `VerificationReport` provenance.
- `make version-file` is now part of `scripts/start-cerid.sh --build` so `get_version()` returns the real version in the MCP image.
- Operators see a one-time `embedding_dim_mismatch` ERROR log per mismatched Chroma collection, pointing at `POST /admin/collections/repair`.
- Claim cache v2 cold-starts existing `verf:claim:*` entries — expect 10-20× latency on first verification pass until the cache rewarms.

## v0.83.0 — Verification Hardening + Memory Efficacy + Bug-Hunt Sprint (2026-04-10 → 2026-04-15)

### Verification Pipeline Hardening (2026-04-13)
- **Round-2 claim sweep** — timed-out claims re-verified in a second pass with full conversation context
- **Expert verification mode** — Grok 4 as dedicated verification model for high-stakes claims (`VERIFICATION_EXPERT_MODEL`)
- **Authoritative external verification** — LLM synthesizes from external data sources rather than parametric memory
- **Graph-guided verification** — Neo4j relationship structure used as evidence for fact-relationship checks
- **Fact-relationship verification** — temporal/entity/specificity alignment validation
- **Dynamic confidence scoring** — per-source tuning (Wikipedia title match boost, Wolfram non-answer detection, DuckDuckGo .gov boost)

### Memory Efficacy (2026-04-13)
- **Source-aware external query construction** — per-source `adapt_query()`/`is_relevant()` with intent-based routing across 7 data sources
- **CRAG retrieval quality gate** — supplements with external sources when top KB relevance < `RETRIEVAL_QUALITY_THRESHOLD` (0.4)
- **Verified-fact-to-memory promotion** — high-confidence verified claims auto-promote to empirical `:Memory` nodes with `VERIFIED_BY` provenance
- **Tiered memory authority boost** — 4-tier system (0.05-0.25) based on verification status and confidence
- **Refresh-on-read memory decay** — Ebbinghaus rehearsal pattern resets `decay_anchor` on retrieval
- **NLI consolidation guard** — prevents semantic drift during memory merges via entailment threshold

### Bug-Hunt Sprint (2026-04-15) — 15 bugs → 8 root causes
- **Embedding singleton** — fixed split instantiation causing dimension mismatch on fresh installs + startup dim-check + `/admin/collections/repair` endpoint
- **Agent activity stream** — `/agents/activity/*` alias router + SSE exponential backoff (500ms base, 30s max) + abort-on-unmount
- **Healthcheck rewrite** — shared `scripts/lib/healthcheck.sh` library with auth-aware Redis/Neo4j checks + Bifrost skip + zombie container cleanup
- **Onboarding polish** — `CERID_SYNC_DIR_HOST` rename (backward-compat fallback), removed `age` from public README prereqs, fixed CONTRIBUTING.md Node/router path drift
- **Verification wiring** — `MIN_VERIFIABLE_LENGTH` FE/BE alignment 200→25, `onSelectForVerification` prop threaded through to `VerificationBadge`
- **UX fixes** — tab title "Cerid Core"→"Cerid AI", KB counter unification (`Showing X of Y`), Knowledge Digest errors drill-through modal with `DigestErrorItem` type

### Dependency Upgrades
- langgraph 0.6 → 1.1 (major)
- neo4j driver 5.28 → 6.1 (major)
- TypeScript 5.9 → 6.0 (major)
- Vite 7 → 8, @vitejs/plugin-react 5 → 6 (major)
- jsdom 28 → 29, lucide-react v0.577 → v1.8
- React 19.2.5, @tanstack/react-query 5.99

### Testing & CI
- **+14 frontend tests** (705 → 719) — verification orchestrator, agent activity stream, KB counter, digest drill-through
- **+4 backend tests** — embedding singleton, startup dim-check, collections repair, agent console router
- Sync manifest hygiene — `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `__pycache__` excluded from public sync
- Dependabot: ignore ESLint majors until react-hooks plugin supports v10, revert chromadb/langgraph upper-bound widening

### Documentation Re-Baseline (2026-04-15)
- Comprehensive audit: all open issues validated against code (zero actual bugs remaining)
- Version aligned across pyproject.toml, package.json, CLAUDE.md, tasks/todo.md
- Test counts updated (2,413 Python / 719 frontend), tool counts corrected (26 = 21 core + 5 trading)
- CI coverage floor corrected in docs (20%, not 70%)
- Stale todo items archived (leapfrog merge completed April 5, all B-CRITICAL/B-HIGH resolved)

## v0.82.0 — Unified Implementation Plan + Phase C Architecture (2026-04-05 → 2026-04-10)

### Phase C: Core Extraction + NLI Architecture (2026-04-08 → 2026-04-10)
- **Core/App split** — portable orchestrator core (`core/`) separated from application layer (`app/`). Bridge modules in `agents/`, `utils/`, `services/` re-export for backward compat.
- **`*_internal.py` pattern** — 7 Python files + 1 TypeScript file hold internal-only code; an internal bootstrap module registers the corresponding private routers at startup.
- **NLI entailment service** — `core/utils/nli.py` (ONNX, <10ms) powers verification, Self-RAG, RAGAS, and RAG pipeline claim validation.
- **Sync manifest** — `.sync-manifest.yaml` declares internal-only files, mixed files (hook markers), and forbidden strings for automated repo sync via `scripts/sync-repos.py`.
- **Contract ABCs** — `core/contracts/` defines VectorStore, GraphStore, CacheStore, LLMClient interfaces.
- **Concrete stores** — `app/stores/` implements ChromaVectorStore, Neo4jGraphStore, RedisCacheStore.
- **Source authority** — chat transcripts discounted 0.35x, memories retain full relevance.

### Post-Phase: Dependency Cleanup + Remaining Items
- **Dependency cleanup** — removed 8 unused deps (stripe/public, faster-whisper, requests, structlog/public, pytesseract, Pillow, bcrypt, PyJWT). Docker image 4.09→3.18 GB. Dependabot 33→2 vulns.
- **packages/desktop/** removed from public repo (kept in internal)
- **B31: Conversation grouping** — feedback from same conversation_id appends to existing KB artifact
- **B33: Feedback buttons** — ThumbsUp/ThumbsDown on assistant messages (POST /artifacts/{id}/feedback)
- **B35: Model compliance note** — footer in model selector about non-US model availability
- **B36: File picker** — browse button on archive path using File System Access API
- **Memory system fix** — get_collection → get_or_create_collection (fixes 500 on fresh installs)
- **Configurable model preload** — `CERID_PRELOAD_MODELS=false` Dockerfile ARG for smaller images
- **Startup prerequisites** — python3, curl, port availability, Docker memory checks
- **CI fixes** — test mock targets (requests→httpx), import sorting (I001), BLE001 suppressions

### Phase 1: Tiered Inference Detection
- **InferenceConfig singleton** — auto-detects platform (macOS ARM/Intel, Linux, Windows), GPU (Metal/CUDA/ROCm/DirectML), Ollama, and FastEmbed sidecar at startup
- **Dynamic ONNX providers** — embeddings.py and reranker.py use detected GPU providers instead of hardcoded CPU
- **Health endpoint** — `/health` now includes `inference` field with provider, tier, GPU, latency
- **Performance baseline** — documented in `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md`

### Phase 2: FastEmbed Sidecar + UX Polish
- **Sidecar server** — `scripts/cerid-sidecar.py` wraps ONNX embed/rerank with native GPU acceleration
- **Sidecar installer** — `scripts/install-sidecar.sh` auto-detects platform and GPU for correct onnxruntime variant
- **Sidecar HTTP client** — `utils/inference_sidecar_client.py` with circuit breaker and latency tracking
- **B18: Sub-menu formatting** — consistent padding (p-2), font-weight, separator spacing across all toolbar popovers
- **B23: Recent imports scroll** — collapsible list, 4 default visible, "Show N more" expandable
- **B26: Health dashboard** — grouped by Infrastructure / AI Pipeline / Optional with section headers and auto-refresh
- **B30: External search debugging** — structured logging in `DataSourceRegistry.query_all()`
- **HNSW tuning** — ChromaDB M=12, EF_CONSTRUCTION=400 for better recall on new collections
- **Reranker warmup gating** — skipped when RERANK_MODE=none (~1s faster startup)
- **Ollama pool** — keep-alive connections increased 5→8

### Phase 3: GUI Integration + Recheck Loop
- **Inference tier in Settings** — green/blue/yellow badge showing optimal/good/degraded with provider name
- **Periodic re-check** — background loop every 300s detects Ollama start/stop, emits SSE event
- **Ollama wizard UX** — CPU-only warning, platform-specific install commands (brew/curl), copy buttons

### Phase 4: Ollama LLM Routing + B-LOW Items
- **ai_categorize() routing** — routes through `call_internal_llm()` when INTERNAL_LLM_PROVIDER=ollama
- **contextualize_chunks() routing** — same internal LLM routing for free local inference
- **B32: Synopsis regeneration** — `POST /artifacts/regenerate-all-synopses` with background processing
- **B33: Feedback loop design** — `docs/FEEDBACK_LOOP_DESIGN.md` (opt-in per conversation, quality gates)
- **B41: KB title editing** — already implemented (inline-editable with double-click + PATCH)

### Phase 5: Wiring Checks + Final Audit
- All 8 subsystem wiring checks passed (setup, chat, KB, external API, settings, health, memory, analytics)
- Regulated-deployment compliance verified (no Chinese-origin AI references)
- Documentation updated (CLAUDE.md, CHANGELOG.md)

### New Files
- `src/mcp/utils/inference_config.py` — tiered inference detection
- `src/mcp/utils/inference_sidecar_client.py` — sidecar HTTP client
- `scripts/cerid-sidecar.py` — FastEmbed sidecar server
- `scripts/install-sidecar.sh` — platform-aware installer
- `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md` — performance baseline
- `docs/FEEDBACK_LOOP_DESIGN.md` — feedback loop design doc

## v0.81 — Beta Test Implementation (2026-04-04)

### Phase 1 (P0 — Critical Path)
- **PDF Drag-Drop & Ingestion** — Fix macOS file handler interception, add ChromaDB write-flush check, add `skip_quality` for faster wizard ingestion
- **Provider Detection** — Strip env var quotes, add unified `detect_provider_status()`, structured validation errors
- **Dev Tier Switch** — Hidden in production builds
- **Quality Scoring v2** — 6-dimension domain-adaptive scoring (richness, metadata, freshness, authority, utility, coherence), star/evergreen support
- **Preview Fix** — Handle external artifacts and malformed `chunk_ids` gracefully
- **Wizard Cleanup** — Remove Domains card, rename step to "Storage & Archive"

### Phase 2 (P1 — Usability & Polish)
- **Wizard Overhaul** — Optional Features step (Ollama + data sources), Bifrost hidden from health, health tooltips and fix actions
- **Custom LLM** — Custom OpenAI-compatible provider input, credits link, usage explainer
- **Chat UX** — Plain-language tooltips on all toolbar controls, privacy color escalation (green→red), verification cost explainer
- **KB Improvements** — MessageSquarePlus icon, chunk tooltip, star/evergreen buttons
- **Settings Polish** — Chunk size tooltip, cursor-default on Row, section state version bump

### Phase 3 (P2 — Backlog)
- **External Enrichment** — Enrich button on chat messages (Globe icon)
- **Console Consistency** — Read-only RAG mode display, pulse animation on unread badge
- **Custom API Wizard** — CustomApiSource backend (3 auth modes), CustomApiDialog frontend

### New Files
- `src/web/src/components/setup/optional-features-step.tsx`
- `src/web/src/components/setup/custom-provider-input.tsx`
- `src/web/src/components/kb/custom-api-dialog.tsx`
- `src/mcp/utils/data_sources/custom.py`

## [0.81] - 2026-04-03

### Features
- **Eval router wired up** — `POST /api/eval/run` and `GET /api/eval/benchmarks` now registered in main.py (self-gated by `CERID_EVAL_ENABLED`) (`f5bfc28`)
- **Typed Redis wrapper** — `utils/typed_redis.py` provides properly narrowed return types for sync `redis.Redis`, eliminating 57 mypy errors in one place (`4400bdf`)
- **Response model annotations** — 77 endpoints across 15 routers now have `response_model=` for proper OpenAPI schema generation. 13 new Pydantic model files under `models/` (`05b84ec`, `e3a3988`)
- **Code AST parser activated** — `parsers/code_ast.py` `@register_parser` decorators now fire via `__init__.py` import (`1cdc94d`)
- **Setup wizard** — 8-step onboarding with provider routing intelligence, degradation awareness, and health dashboard (`07a64a6`, `c09d2f6`, `b3fc202`)

### Bug Fixes
- **custom_agents pagination** — `total` field now returns actual DB count via `count_agents()` Cypher, not page size (`7aa7059`)
- **custom_agents query delegation** — passes `model_override`, `top_k`, returns `agent_config` with system_prompt/temperature/rag_mode/tools (`7aa7059`)
- **Duplicate endpoint removal** — removed `POST /chat/compress` from `chat.py` (duplicate with incompatible response key) and `GET /plugins` from `health.py` (shadowed by `plugins.py`) (`ef8489c`)
- **Frontend API bugs** — `fetchOpenRouterCredits` fixed to call `/providers/credits` (was 404), `toggleAutomation` fixed to use `/enable`/`disable` endpoints (was 404) (`ef8489c`)
- **error_handler.py** — bare `except: pass` replaced with debug logging for circuit breaker failures (`1cdc94d`)
- **test_ingestion.py** — narrowed bare `except Exception: pass` to specific expected exceptions (`1cdc94d`)
- **Trading mock paths** — 5 stale mock paths in `test_router_sdk.py` updated from `routers.sdk` to `routers.agents` (`77669a0`)
- **TOC test** — updated for `queueMicrotask`-based heading scan (`b36f490`)
- **Docker deployment** — resolved crashes when running without Bifrost (`02e979d`)

### Code Quality
- **ESLint warnings** — resolved all 28 warnings across 24 frontend files: 12 set-state-in-effect, 7 only-export-components, 5 exhaustive-deps, plus purity/ref/directive fixes (`2229d7e`)
- **Mypy errors** — 59 → 2 (only unrelated `multimodal.py` stubs remain) via `TypedRedis` wrapper (`4400bdf`)
- **Ruff lint** — 0 errors across 199+ Python files (maintained)
- **Dead code removed** — `utils/a2a_client.py`, `utils/agent_activity.py`, `utils/content_filter.py`, `tokenize_lower()` from `text.py` (`ad1ff81`)

### Documentation
- **CLAUDE.md** — CI jobs 8→6, coverage 70%→60%, test counts updated, agent list completed (`06b950a`)
- **API_Reference.md** — removed 10 phantom endpoints (trading proxy, boardroom SDK), added 18 real endpoints (custom agents, plugin registry, system monitor, webhooks), marked billing as internal (`56515ef`)

### Infrastructure
- **CI fixes** — multiple rounds of lint, typecheck, and test stabilization after setup wizard merge (`fa9b9df`, `9d354dd`, `9ff9ea0`, `e496922`, `98dc16e`, `bb0a981`)

### New Files
- `src/mcp/utils/typed_redis.py` — typed Redis facade (35 methods)
- `src/mcp/models/agents_response.py` — 14 response models for agent endpoints
- `src/mcp/models/artifacts.py` — 7 response models for artifact endpoints
- `src/mcp/models/data_sources.py` — 11 response models for data source endpoints
- `src/mcp/models/digest.py` — 4 response models
- `src/mcp/models/ingestion.py` — 6 response models
- `src/mcp/models/memories.py` — 4 response models
- `src/mcp/models/query.py` — 2 response models
- `src/mcp/models/settings.py` — 3 response models
- `src/mcp/models/taxonomy.py` — 5 response models
- `src/mcp/models/upload.py` — 4 response models
- `src/mcp/models/user_state.py` — 3 response models
- `src/mcp/models/watched_folders.py` — 3 response models
- `src/mcp/models/webhooks.py` — 5 response models
