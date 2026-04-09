# Consolidated Remaining Issues — Cerid AI

**Date:** 2026-04-05
**Source:** Live debug sessions + FINAL_PUNCH_LIST_V2 cross-reference
**Repo:** `cerid-ai-public` (`~/Develop/cerid-ai-public`)

---

## A) FIXED — Need Container Rebuild + Browser Clear to Verify

These items have code fixes committed but the running Docker containers / browser cache may be stale.

| # | Issue | Fix | Commit/File | Verify How |
|---|-------|-----|-------------|------------|
| A1 | Preconfigured key detection — only one sentinel matched | Match both `(configured)` and `(from .env)` | `9e44a23` ��� `setup-wizard.tsx` | Rebuild web container, hard-refresh, check all 4 providers show as preconfigured |
| A2 | `.env.example` missing Anthropic/xAI key entries | Added entries to `.env.example` | `96801d1` — `.env.example` | Verify file has `ANTHROPIC_API_KEY` and `XAI_API_KEY` entries |
| A3 | Provider detection shows only 2 of 4 providers | Root cause: keys not in `.env`, not code bug | Audit confirmed code is correct | Add `ANTHROPIC_API_KEY` and `XAI_API_KEY` to `.env`, restart MCP container |
| A4 | Wizard "Query failed" in Try It Out | `handleQuery` in `first-document-step.tsx` calls `queryKB()` — requires working LLM | Fix: valid API key + rebuild | After rebuild, upload a doc, then query — should get answer |
| A5 | "Stream interrupted" in chat | LLM call failure during SSE streaming | Fix: valid API key restores LLM calls | Send a chat message after API key is working |
| A6 | Verification shows "offline" / "no claims" at startup | Self-test fails without LLM, caches failure for 24h | Multiple fixes in `97106fb` (see S2 below) | After key is set, trigger `/setup/retest-verification` |

**Action to verify all A items:**
```bash
# 1. Ensure .env has valid keys for all 4 providers
# 2. Rebuild and restart
cd ~/Develop/cerid-ai-public
./scripts/start-cerid.sh --build
# 3. Hard-refresh browser (Cmd+Shift+R)
# 4. Walk through wizard, verify each step
```

---

## B) NEED CODE CHANGES — Exact File and Line

### B-CRITICAL (blocks core functionality)

| # | Issue | File | Line(s) | Change Required |
|---|-------|------|---------|-----------------|
| B1 | Heuristic claim extractor misses simple claims | `src/mcp/agents/hallucination/patterns.py` | (new patterns) | Add regex patterns for `"X is a/an Y"` and `"X was created/founded/developed by Y"` sentences. The self-test phrase "Python is a programming language created by Guido van Rossum" must extract >=1 claim. **[Punch S2.3]** |
| B2 | SSE verify-stream has no error event on exception | `src/mcp/agents/hallucination/streaming.py` | wrap `verify_response_streaming()` | Add try/except around generator. On exception, yield `{"event": "error", "data": {"message": str(exc)}}` before returning. Prevents "stream interrupted." **[Punch S2.4]** |
| B3 | Self-test TTL is 24 hours — failed results persist too long | `src/mcp/agents/hallucination/startup_self_test.py` | line ~82 | Change `ex=86400` to `ex=3600` (1 hour). **[Punch S2.6]** |
| B4 | Re-run verification self-test after keys configured | `src/mcp/routers/setup.py` | `configure()` success path | After `POST /setup/configure` succeeds, call `await run_verification_self_test(get_redis())`. **[Punch S2.1]** |
| B5 | Add manual "Re-check" endpoint for verification | `src/mcp/routers/setup.py` | new endpoint | Add `POST /setup/retest-verification` → calls `run_verification_self_test()`, returns result. Wire to health dashboard "Re-check" button. **[Punch S2.2]** |
| B6 | Health dashboard says "offline" instead of "LLM required" | `src/web/src/components/setup/health-dashboard.tsx` | verification status display | When verification is "unavailable" AND no providers configured, show "Requires API key — configure a provider first" instead of generic "Offline". **[Punch S2.7]** |
| B7 | Skip LLM metadata in wizard context (slow ingestion) | `src/mcp/services/ingestion.py` + `src/mcp/routers/upload.py` | upload endpoint params | Add `skip_metadata: bool = Query(False)`. When true, use first 200 chars as summary, filename words as keywords. Frontend wizard passes `?skip_metadata=true`. **[Punch S2.5]** |

### B-HIGH (wiring / UX gaps)

| # | Issue | File | Line(s) | Change Required |
|---|-------|------|---------|-----------------|
| B8 | `onEnrich` not wired — enrichment buttons missing | `src/web/src/components/chat/chat-panel.tsx` or message list renderer | grep for `onEnrich=` returns zero | Create `handleEnrich` in chat panel, pass to every assistant `MessageBubble`. **[Punch S1: message-bubble.tsx onEnrich]** |
| B9 | `onSelectForVerification` may be unwired | `src/web/src/components/chat/chat-messages.tsx` | verify prop threading | Confirm prop is threaded from parent to `MessageBubble`. Test click-to-verify on previous messages. **[Punch S1]** |
| B10 | Artifact card expand shows no additional content | `src/web/src/components/kb/artifact-card.tsx` | expand toggle | Add expanded view: keyword tags, metadata row, quality breakdown, chunk list. **[Punch S1 + Beta #17]** |
| B11 | Sidebar console activity LED not implemented | `src/web/src/components/layout/sidebar.tsx` | N/A (new) | Create `ActivityContext` or lightweight emitter, add LED dot to sidebar pane items. **[Punch S1]** |
| B12 | `CustomProviderInput` `onValidated` is no-op | `src/web/src/components/setup/setup-wizard.tsx` | line ~600 | Wire `onValidated` callback to persist custom provider state. **[Punch #5]** |
| B13 | Quote stripping missing in `get_configured_providers()` | `src/mcp/config/providers.py` | line ~194 | Change `api_key = os.getenv(env_var, "")` to `api_key = os.getenv(env_var, "").strip().strip('"').strip("'")`. **[Punch S3.1]** |
| B14 | Dated Anthropic model names in providers.py | `src/mcp/config/providers.py` | lines 60-62 | Model list uses `claude-sonnet-4-20250514` etc. These are correct for Anthropic direct API, but should be noted. The validation at line 125 correctly uses `claude-haiku-4-20250514` for direct Anthropic API test — this is fine. No change needed unless Anthropic API changes. |
| B15 | virtiofs Errno 35 handling inconsistent | `src/mcp/sync/status.py`, `src/mcp/services/ingestion.py` | various | Catch `OSError` errno 35, retry with backoff. **[Punch S4.1, S4.2]** — New util at `src/mcp/utils/virtiofs_retry.py` already exists, needs wiring. |
| B16 | Neo4j cartesian product in categorization query | `src/mcp/` (graph query for artifact categorization) | MERGE query | Change `MATCH (a:Artifact {id: $aid}), (sc:SubCategory ...)` to use `MATCH (a:Artifact {id: $aid}) MATCH (sc:SubCategory ...)` or add relationship. Low priority — performance concern only. |

### B-MEDIUM (UX improvements)

| # | Issue | File | Change Required |
|---|-------|------|-----------------|
| B17 | Expert verification "premium" badge incorrect | Verification submenu component | Remove "premium" label from expert verification. **[Beta #9]** |
| B18 | Sub-menus need formatting/tooltip audit | Chat toolbar popovers | Consistent padding, font sizes, dividers, tooltips on all items. **[Beta #12]** |
| B19 | Chat names not editable | Chat header + conversation list | Add inline-editable title. On save, call `PATCH /conversations/{id}`. **[Beta #21]** |
| B20 | No trash/archive on chat history | `src/web/src/hooks/use-conversations.ts` + conversation list | Add archive/delete buttons, `archived` field on conversation model. **[Beta #13]** |
| B21 | KB cards need replace/re-generate buttons | `src/web/src/components/kb/artifact-card.tsx` | Add "Replace file" and "Re-generate synopsis" buttons. **[Beta #18]** |
| B22 | DOCX import error not surfaced to frontend | `src/mcp/routers/upload.py` + frontend | Include exception message in error response. Add `.doc` → "Unsupported: convert to .docx" message. **[Beta #22]** |
| B23 | Recent imports max 4, no "show more" | KB import list component | Default collapsed, max 4 visible, "Show N more" link. **[Beta #23]** |
| B24 | Sort options misplaced above artifacts | KB artifact list | Move sort row above artifact list, below upload actions. **[Beta #24]** |
| B25 | Auto-generate synopsis on import fallback | `src/mcp/services/ingestion.py` | If `extract_metadata()` fails (no LLM), use first 200 chars as fallback summary. **[Beta #26]** |
| B26 | Health tab layout poor | `src/web/src/components/setup/health-dashboard.tsx` | Redesign: group into categories, card layout, auto-refresh 30s. **[Beta #25]** |
| B27 | Settings: unclear editable vs read-only | Settings components | Visual distinction: hover highlight + cursor for editable, lock icon for read-only. **[Beta #30]** |
| B28 | Pipeline stages not explained | `src/web/src/components/settings/pipeline-section.tsx` | Add info icon + tooltip per stage explaining what it does. **[Beta #31]** |
| B29 | Non-binary settings need recommended configs | Settings sliders/dropdowns | Add "Recommended" indicator with highlighted range. **[Beta #34]** |
| B30 | External search returns no results | `src/mcp/` (DataSourceRegistry) | Debug `DataSourceRegistry.query_all()`, check enabled sources, add logging. **[Orig #42]** |

### B-LOW (P2 / Enhancement)

| # | Issue | Change Required |
|---|-------|-----------------|
| B31 | Chat queries create separate KB items | Add `conversation_id` grouping to Artifact model. **[Beta #19]** |
| B32 | "Re-generate all synopses" button | Add to Health tab: `POST /artifacts/regenerate-all-synopses`. **[Beta #27]** |
| B33 | Feedback loop purpose unclear | Design doc needed before UX. Make opt-in per conversation. **[Beta #33]** |
| B34 | 350 models, no management UX | Virtual scrolling, search/filter, sort, per-model pricing. **[Beta #35]** |
| B35 | Chinese models via OpenRouter policy | USG compliance = bundled/default only. OpenRouter passthrough should allow any. Add disclaimer. **[Beta #15]** |
| B36 | Storage: no file picker (OS dialog) | Replace text input with file picker. **[Beta #2]** |
| B37 | Ollama wizard: no VRAM detection | Parse `nvidia-smi --query-gpu=memory.total`. **[Punch 5.4]** |
| B38 | Ollama wizard: no CPU-only penalty | If no GPU and not Apple Silicon, reduce model rec by one tier. **[Punch 5.4]** |
| B39 | Ollama wizard: no inference speed estimates | Add `expected_tokens_per_sec` field per model in recommendations. **[Punch 5.4]** |
| B40 | Ollama wizard: no download progress bar | Parse Ollama pull API streaming response. **[Punch 5.4]** |
| B41 | KB title not editable in card | Add inline-editable title. `PATCH /artifacts/{id}`. **[Beta #20]** |

---

## C) CROSS-REFERENCE: FINAL_PUNCH_LIST_V2 Coverage

### Section 1: Systemic Fixes

| Systemic Fix | Status | Items Above |
|-------------|--------|-------------|
| **S1** Parent-Child Wiring Audit | NEEDS WORK | B8, B9, B10, B11, B12 |
| **S2** LLM Dependency Cascade | PARTIALLY CODED (commit `97106fb`), NEEDS VERIFY | B1-B7, A6 |
| **S3** Unify Provider Detection | PARTIALLY FIXED | A1, A3, B13 |
| **S4** macOS Docker virtiofs | UTIL CREATED, NEEDS WIRING | B15 |

### Section 2: Individual Issue Fixes

| Punch # | Status | Item Above |
|---------|--------|------------|
| Orig #4 (Test buttons) | Fixed by S3.4/S3.5 + A1 | A1 |
| Orig #9 (Review shows 2 providers) | Fixed by S3.3 + A3 | A3 |
| Orig #10 (Key detection inconsistent) | Fixed by S3.1-S3.3 | A3, B13 |
| Orig #45 (Settings providers offline) | Fixed by S3.1 | B13 |
| Beta #2 (Location selector) | NOT STARTED | B36 |
| Beta #3 (Ollama hardware recs) | NOT STARTED | B37-B40 |
| Beta #4 (Review fix actions) | PARTIAL — S3 helps | A1, A3 |
| Beta #5 (Verification offline) | Fixed by S2.1/S2.2/S2.7 | B4, B5, B6, A6 |
| Beta #6 (25s parse, query fails) | PARTIAL — S2.5 for speed | B7, A4 |
| Beta #7 (Enrichment missing) | Fixed by S1 wiring | B8 |
| Beta #9 (Premium badge incorrect) | NOT STARTED | B17 |
| Beta #10 (No claims to verify) | Fixed by S2.3/S2.4 | B1, B2 |
| Beta #11 (Stream interrupted) | Fixed by S2.4 | B2, A5 |
| Beta #12 (Sub-menu formatting) | NOT STARTED | B18 |
| Beta #13 (No trash/archive chat) | NOT STARTED | B20 |
| Beta #15 (Chinese models) | NOT STARTED | B35 |
| Beta #16 (Select previous broken) | Fixed by S1 wiring | B9 |
| Beta #17 (KB expansion broken) | Fixed by S1 + content needed | B10 |
| Beta #18 (KB replace/regen buttons) | NOT STARTED | B21 |
| Beta #19 (Chat ��� KB items) | NOT STARTED (P2) | B31 |
| Beta #20 (KB title editable) | NOT STARTED | B41 |
| Beta #21 (Chat names editable) | NOT STARTED | B19 |
| Beta #22 (DOCX fails silently) | NOT STARTED | B22 |
| Beta #23 (Recent imports max 4) | NOT STARTED | B23 |
| Beta #24 (Sort misplaced) | NOT STARTED | B24 |
| Beta #25 (Health tab layout) | NOT STARTED | B26 |
| Beta #26 (Auto-generate synopsis) | NOT STARTED | B25 |
| Beta #27 (Regen all synopses) | NOT STARTED (P2) | B32 |
| Beta #28 (Tier capabilities) | NOT STARTED | Not listed (P2 UX) |
| Beta #29 (Sync deadlock Errno 35) | UTIL CREATED | B15 |
| Beta #30 (Editable vs read-only) | NOT STARTED | B27 |
| Beta #31 (Pipeline stages) | NOT STARTED | B28 |
| Beta #32 (External sources) | NOT STARTED | B30 |
| Beta #33 (Feedback loop) | NOT STARTED (needs design) | B33 |
| Beta #34 (Recommended configs) | NOT STARTED | B29 |
| Beta #35 (Model management) | NOT STARTED (P2) | B34 |

### Section 3: Wiring Checks — NOT STARTED
Full QA script defined in FINAL_PUNCH_LIST_V2 Section 3. Should be run AFTER all B-CRITICAL and B-HIGH items are completed.

### Section 4: Multi-OS Compatibility — NOT STARTED
macOS/Linux/Windows evaluation defined. Action items listed in punch list.

### Section 5: Ollama Architecture — PARTIALLY EVALUATED
Architecture verified correct. UX gaps identified (B37-B40).

---

## PRIORITY EXECUTION ORDER

```
IMMEDIATE (unblocks everything):
  1. Add valid API keys to .env (OpenRouter, Anthropic, xAI)
  2. Rebuild containers: ./scripts/start-cerid.sh --build
  3. Hard-refresh browser (Cmd+Shift+R)
  4. Verify A1-A6 are resolved

WEEK 1 — Critical Code Fixes (B1-B7):
  Day 1: B1 (claim patterns) + B2 (SSE error events) + B3 (TTL reduction)
  Day 1: B4 (retest after configure) + B5 (recheck endpoint)
  Day 2: B6 (health dashboard LLM message) + B7 (skip metadata in wizard)
  Day 2: Verify S2 complete — run hallucination self-test, verify claims extracted

WEEK 1 — High Wiring Fixes (B8-B16):
  Day 3: B8 (onEnrich wiring) + B9 (onSelectForVerification) + B10 (artifact expand)
  Day 4: B11 (activity LED) + B13 (quote stripping) + B15 (virtiofs wiring)
  Day 4: B12 (custom provider persist)
  Day 5: Run Section 3 wiring checks (wizard flow, chat pipeline, KB pipeline)

WEEK 2 — Medium UX (B17-B30):
  Day 6: B17-B19 (chat UX: badge, menus, editable names)
  Day 7: B21-B25 (KB UX: replace, DOCX errors, imports, sort)
  Day 8: B26-B29 (settings UX: health redesign, pipelines, recommended)
  Day 9: B30 (external search debug)
  Day 10: Full QA pass (Section 3 wiring checks)

WEEK 3 — P2/Enhancement (B31-B41):
  Ollama UX (B37-B40), Model management (B34), Chat archive (B20),
  KB conversation grouping (B31), Synopsis regen (B32), Feedback design (B33)
```

---

*Consolidated from: live-debug-results.md, fix-verification-audit.md, FINAL_PUNCH_LIST_V2_2026-04-04.md*
*Total: 6 fixed items (A), 41 code change items (B), 100% punch list coverage (C)*
