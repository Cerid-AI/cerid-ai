# Cerid AI — Aggregated Punch List Report

**Date:** 2026-04-04
**Source:** Original 50-item punch list (2026-04-03), Implementation Plan (WP1-WP13), New Beta Test (2026-04-04)
**Git Range:** `54cf9f4..dc8ded3` (all v0.81 implementation commits)

---

## Executive Summary

- **Original punch list:** 50 items across 3 phases (P0/P1/P2)
- **Implementation commits:** 13 work packages (WP1-WP13) all committed
- **Items fully completed (code committed):** ~38 of 50
- **Items partially completed:** ~7
- **Items not actioned or ineffective:** ~5
- **New beta test findings:** 35 items reported
  - **Still broken (supposed to be fixed):** ~12
  - **Genuinely new issues:** ~15
  - **Regressions:** ~3
  - **Already tracked / duplicate:** ~5

---

## PART A: Original 50-Item Punch List Status

### Legend
- **DONE** — Code committed and verified in git log
- **PARTIAL** — Code committed but beta test shows it's not fully working
- **NOT WORKING** — Code committed but beta test confirms it's still broken
- **NOT ACTIONED** — No evidence of implementation

---

### A. Setup Wizard — Welcome (Items 1-3)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 1 | Remove Ollama from welcome system check → Optional Features | — | WP6 | `7785890` | **DONE** — Optional Features step created |
| 2 | Memory detection edge cases (HOST_MEMORY_GB) | — | — | `2c8b20c` | **DONE** — Fixed in earlier commit (host RAM instead of Docker VM) |
| 3 | Docker install guide renders outside Docker | — | — | `3bca4fc` | **DONE** — Fixed in earlier commit |

### B. Setup Wizard — API Keys (Items 4-10)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 4 | Fix Test buttons — backend validation + spinner | HIGH | WP2 | `4b4f99e` | **NOT WORKING** — Beta #1: test buttons and see/hide still non-functional |
| 5 | Add multi-provider / custom LLM option | HIGH | WP7 | `1eed52c` | **DONE** — Custom provider input created |
| 6 | Add OpenRouter "Add Credits" link | — | WP7 | `1eed52c` | **DONE** |
| 7 | Add usage rate explainer | — | WP7 | `1eed52c` | **DONE** |
| 8 | Remove Ollama info box from Keys page | — | WP7 | `1eed52c` | **DONE** |
| 9 | Fix Review & Apply showing "Not configured" for existing keys | — | WP5 | `e986919` | **NOT WORKING** — Beta #4: Only OpenRouter and OpenAI show as ready despite xAI and Anthropic working |
| 10 | End-to-end key detection consistency | — | WP2 | `4b4f99e` | **PARTIAL** — Unified detection added but still inconsistent per beta #4 |

### C. Setup Wizard — Structure & Flow (Items 11-18)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 11 | Remove Domains card from wizard | HIGH | WP5 | `e986919` | **DONE** — Renamed to "Storage & Archive" |
| 12 | Create Optional Features wizard card | HIGH | WP6 | `7785890` | **DONE** — `optional-features-step.tsx` created |
| 13 | Remove/hide Bifrost from wizard | — | WP6 | `7785890` | **DONE** |
| 14 | Reframe "Simple" → "Clean & Simple" | — | WP6 | `7785890` | **DONE** |
| 15 | Mode card: reflect configured providers and KB state | — | WP6 | `7785890` | **DONE** |
| 16 | Move optional services note to Optional Features card | — | WP6 | `7785890` | **DONE** |
| 17 | Service Health: plain language tooltips | — | WP6 | `7785890` | **DONE** — But beta #5 says verification shows offline |
| 18 | Service Health: actionable fix buttons | — | WP6 | `7785890` | **PARTIAL** — Code committed but beta #4 says errors should have fix actions |

### D. Setup Wizard — Try It Out (Items 19-21)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 19 | Fix PDF drag-drop (activates Adobe Acrobat) | CRITICAL | WP1 | `1024341` | **PARTIAL** — Drag-drop fix committed; beta #6 still reports ~25s parse time and query failures |
| 20 | Fix PDF query failure (ingestion succeeds, query fails) | CRITICAL | WP1 | `1024341` | **NOT WORKING** — Beta #6: queries still fail with "query failed" |
| 21 | Optimize ingestion <5s for 2-page PDF | HIGH | WP1 | `1024341` | **NOT WORKING** — Beta #6: still ~25 seconds |

### E. Main GUI — Chat (Items 22-31)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 22 | Add tooltips to all chat toolbar icons | HIGH | WP8 | `989b1de` | **DONE** |
| 23 | Explain injection threshold with tooltips | HIGH | WP8 | `989b1de` | **DONE** |
| 24 | Explain RAG modes with plain language | HIGH | WP8 | `989b1de` | **DONE** |
| 25 | Consistent markup/rendering across panels | — | WP8 | `989b1de` | **DONE** |
| 26 | Expert mode: show cost instead of warning | — | WP8 | `989b1de` | **DONE** |
| 27 | Add verification dashboard activation | — | WP8 | `989b1de` | **PARTIAL** — Beta #11: dashboard shows "stream interrupted" |
| 28 | Privacy mode: visual escalation (green→red) | — | WP8 | `989b1de` | **DONE** |
| 29 | Knowledge console: fix duplicate mode selector | — | WP12 | `bcf3e85` | **DONE** — Read-only display in console |
| 30 | Knowledge console: consistent settings button | — | WP12 | `bcf3e85` | **DONE** |
| 31 | Add external enrichment button on chat bubbles | — | WP11 | `bcf3e85` | **NOT WORKING** — Beta #7: chat bubble integrations not present |

### F. Main GUI — Display Issues (Items 32-34)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 32 | Remove developer tier switch from public build | HIGH | WP3 | `6823dcd` | **DONE** |
| 33 | Explain "tokens remaining" in metrics | — | WP8 | `989b1de` | **DONE** |
| 34 | Add console activity LED | — | WP12 | `bcf3e85` | **NOT WORKING** — Beta #14: console activity "light" not visible |

### G. Knowledge Base Tab (Items 35-44)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 35 | Fix KB quality scoring | HIGH | WP4 | `b95b67a` | **DONE** — Quality v2 with 6-dimension scoring |
| 36 | Fix "Preview content" failure | HIGH | WP4 | `b95b67a` | **DONE** — Handles external artifacts + malformed chunk_ids |
| 37 | Fix plus icon meaning → MessageSquarePlus | — | WP9 | `12e0bbd` | **DONE** |
| 38 | Add tooltip to chunk count badge | — | WP9 | `12e0bbd` | **DONE** |
| 39 | Make KB cards expandable (~2x height) | — | WP9 | `12e0bbd` | **NOT WORKING** — Beta #17: still no way to expand KB cards |
| 40 | Make Upload/Import more prominent | — | WP9 | `12e0bbd` | **DONE** |
| 41 | Add descriptive tooltips to all interactive elements | — | WP9 | `12e0bbd` | **DONE** |
| 42 | Fix external search (returns no results) | — | WP11 | `bcf3e85` | **NOT ACTIONED EFFECTIVELY** — External search issues persist per beta #32 |
| 43 | Add "custom API" option in External section | — | WP13 | `bce94a5` | **DONE** — `custom-api-dialog.tsx` + backend created |
| 44 | External section: default expanded, sub-categories collapsed | — | WP9 | `12e0bbd` | **DONE** |

### H. Settings Page (Items 45-50)

| # | Description | Severity | WP | Commit | Status |
|---|-------------|----------|-----|--------|--------|
| 45 | Fix providers showing offline despite keys in .env | HIGH | WP2 | `4b4f99e` | **NOT WORKING** — Beta #4: still misdetecting xAI and Anthropic |
| 46 | Add info icons for chunk size/overlap | — | WP10 | `6b871b3` | **DONE** |
| 47 | Add tooltips to all non-obvious settings | — | WP10 | `6b871b3` | **DONE** |
| 48 | Fix inconsistent card expand/collapse defaults | — | WP10 | `6b871b3` | **DONE** |
| 49 | Show domain tags on API mouseover | — | WP10 | `6b871b3` | **DONE** |
| 50 | Fix click affordance | — | WP10 | `6b871b3` | **DONE** |

---

### Summary: Original 50 Items

| Status | Count | Items |
|--------|-------|-------|
| **DONE** | 34 | 1,2,3,5,6,7,8,11,12,13,14,15,16,22,23,24,25,26,28,29,30,32,33,35,36,37,38,40,41,43,44,46,47,48,49,50 |
| **PARTIAL** | 6 | 10,17,18,19,27,34 |
| **NOT WORKING** | 8 | 4,9,20,21,31,39,42,45 |
| **NOT ACTIONED** | 2 | (42 was attempted via WP11 but ineffective; all others had commits) |

---

## PART B: New Beta Test Findings (2026-04-04)

### Classification Key
- **REGRESSION** — Was working or not reported before; now broken
- **STILL BROKEN** — Was in original 50 items; fix didn't land
- **NEW** — Genuinely new issue not in original list
- **EVOLVED** — Related to an original item but expanded scope

---

## CRITICAL — Blocks Beta Testing

| Beta # | Issue | Classification | Original # | Notes |
|--------|-------|---------------|------------|-------|
| 6 | Try It Out: ~25s to parse 2-page PDF, queries fail with "query failed" | **STILL BROKEN** | #20, #21 | WP1 committed `skip_quality` and ChromaDB flush check but ingestion speed and query reliability still failing. Root cause likely not fully addressed. |
| 20 | PDF query failure: ingestion succeeds, query returns "query failed" | **STILL BROKEN** | #20 | Same as above — core query path broken after wizard ingest |

## HIGH — Major Usability Blockers

| Beta # | Issue | Classification | Original # | Notes |
|--------|-------|---------------|------------|-------|
| 1 | Keys page: Test buttons and see/hide buttons don't do anything | **STILL BROKEN** | #4 | WP2 (`4b4f99e`) committed fix but buttons remain non-functional. Likely a wiring issue — handler may not be bound, or backend validation endpoint unreachable. |
| 4 | Review & Apply: Only shows OpenRouter + OpenAI despite xAI + Anthropic configured | **STILL BROKEN** | #9, #45 | WP2+WP5 added `detect_provider_status()` and quote stripping. Still not detecting all providers. May need provider registry key mapping fix. |
| 5 | Service Health: Verification shows as offline (shouldn't be) | **STILL BROKEN** | #17 | WP6 added tooltips but verification pipeline health check may have incorrect endpoint or logic |
| 7 | Chat bubble enrichment buttons not present | **STILL BROKEN** | #31 | WP11 (`bcf3e85`) committed enrichment button but beta says not visible. Possible: component not rendered, or behind feature gate. |
| 8 | Knowledge Console pane missing fixed config/interface options at top | **STILL BROKEN** | #29, #30 | Beta says "none of the fixes for this area were actioned." WP12 committed changes — may not have been effective, or deployed build is stale. |
| 10 | Verification: "Verify last response" shows "no factual claims to verify" (incorrect) | **NEW** | — | Claim extraction failing — verification pipeline may not be parsing the response correctly |
| 11 | Verification dashboard shows "stream interrupted" | **PARTIAL (27)** | #27 | WP8 added dashboard but streaming connection appears to break |
| 17 | No way to expand KB cards (still!) | **STILL BROKEN** | #39 | WP9 (`12e0bbd`) committed expand logic but it's not working in practice |
| 22 | Importing a docx failed (all options, no explanation). RTF worked. | **NEW** | — | Parser issue — docx parser may be failing silently. RTF goes through different path. |
| 29 | Sync status shows "[Errno 35] Resource deadlock avoided" | **NEW** | — | OS-level file lock contention — likely Dropbox sync competing with archive watcher |
| 35 | Model management: 350 models, no scroll/sort/filter/add, no update detection | **NEW** | — | Model list UX completely missing — needs full list management component |

## MEDIUM — Significant UX Issues

| Beta # | Issue | Classification | Original # | Notes |
|--------|-------|---------------|------------|-------|
| 2 | Storage/Archive page: needs location-selector, multi-computer config, DB config | **EVOLVED** | #11 | Original only removed Domains. New requirement: make Storage & Archive page more comprehensive with file picker, multi-machine explanation, and database config. |
| 3 | Ollama page: needs semi-automated wizard with OS/hardware detection, experience explanation | **EVOLVED** | #12 | Original created Optional Features card. New requirement: much richer Ollama setup flow with hardware detection and recommended configs. |
| 9 | Verification: expert line item should NOT have "premium" badge | **NEW** | — | Incorrect badge labeling in verification submenu |
| 12 | Sub-menus need visual formatting and tooltip audit | **NEW** | — | General sub-menu quality issue — may overlap with tooltip work but distinct from chat toolbar tooltips |
| 14 | Console activity "light" not visible at bottom | **STILL BROKEN** | #34 | WP12 (`bcf3e85`) added LED but not visible — CSS issue or wrong container |
| 15 | Chinese model info not in shipping code but users should be able to select Chinese models via OpenRouter | **NEW** | — | USG compliance removed Chinese models but users should be able to access them via OpenRouter passthrough. Need policy/UX decision. |
| 16 | Selecting previous responses doesn't work; verification stuck on "no claims" | **REGRESSION** | — | Response selection broken — possible state management issue introduced during WP8 changes |
| 18 | KB cards need: replace file button, re-generate AI synopsis button | **NEW** | — | New artifact management actions not in original spec |
| 19 | Each chat query creates separate KB item instead of segments within one item | **NEW** | — | Ingestion architecture issue — conversation queries should group into a single artifact |
| 21 | Chat names should be editable in main window | **NEW** | — | Missing conversation rename UX |
| 23 | Recent imports display: default max 4 expanded, scrollable, resizable handle | **NEW** | — | Import list UX refinement |
| 24 | Sort options row should be right above knowledge artifacts area | **NEW** | — | Layout ordering issue in KB tab |
| 25 | Health tab layout/display quality is poor | **NEW** | — | General health tab visual quality — needs redesign |
| 26 | AI synopsis should auto-generate on file import | **NEW** | — | Missing auto-summarization trigger in ingestion pipeline |
| 28 | Settings System tab: capabilities categorized by tier with status + mouseover | **NEW** | — | Platform capabilities display needs tier-based organization |
| 32 | External data sources inconsistent, no config, archive folder not shown as watched | **EVOLVED** | #42, #44 | Builds on original external search issues — much broader scope |
| 33 | Feedback loop purpose needs investigation | **NEW** | — | Feature needs design clarification before implementation |
| 34 | Non-binary settings: show recommended configs with explanations | **NEW** | — | Settings UX — sliders and dropdowns need recommended value indicators |

## LOW — Polish & Enhancement

| Beta # | Issue | Classification | Original # | Notes |
|--------|-------|---------------|------------|-------|
| 13 | No trash/archive on chat history items (mouseover) | **NEW** | — | Missing conversation management actions |
| 20 | KB item title should be editable in card | **NEW** | — | Artifact title editing missing |
| 27 | Health tab: "re-generate synopsis for all artifacts" button | **NEW** | — | Batch operation for synopsis regeneration |
| 30 | Unclear which settings are editable vs read-only | **EVOLVED** | #50 | Original fixed click affordance but distinction still unclear |
| 31 | Pipeline stages not explained via mouseover | **NEW** | — | Pipeline section needs stage-by-stage tooltips |

---

## PART C: Priority Ranking for Next Sprint

### P0 — Must Fix Before Next Beta (blocks testing)

1. **PDF ingestion speed + query reliability** (Beta #6, #20 / Original #19-21) — Core flow still broken. Need to investigate actual ingestion timing and query path end-to-end.
2. **Test buttons not functional** (Beta #1 / Original #4) — Button click handlers not firing or backend unreachable. Need to verify wiring.
3. **Provider detection incomplete** (Beta #4 / Original #9, #45) — xAI and Anthropic not detected. Check `_KEY_TO_PROVIDER` mapping and `detect_provider_status()`.
4. **Verification shows offline** (Beta #5 / Original #17) — Health check for verification pipeline returning false negative.
5. **Verification "no claims to verify"** (Beta #10, #16) — Claim extraction broken; selecting previous responses broken. Possible regression from WP8.

### P1 — Fix Before Public Beta

6. **KB card expansion still not working** (Beta #17 / Original #39) — Expand logic committed but not functional.
7. **Chat enrichment buttons missing** (Beta #7 / Original #31) — Component committed but not rendered.
8. **Console activity LED not visible** (Beta #14 / Original #34) — CSS or positioning issue.
9. **Knowledge Console config missing** (Beta #8 / Original #29-30) — Fixed config panel at top not rendering.
10. **Verification "stream interrupted"** (Beta #11 / Original #27) — SSE connection dropping.
11. **DOCX import failure** (Beta #22) — Parser error with no user feedback.
12. **Sync deadlock error** (Beta #29) — `[Errno 35]` resource contention.
13. **Storage/Archive page needs location selector** (Beta #2) — Text box insufficient, needs file picker.
14. **Ollama wizard needs hardware-aware recommendations** (Beta #3) — Current setup too bare.
15. **Chinese models via OpenRouter** (Beta #15) — Policy decision needed.

### P2 — Backlog / Enhancement

16. KB card: replace file, re-generate synopsis buttons (Beta #18)
17. Chat queries → single KB item grouping (Beta #19)
18. KB title editable in card (Beta #20)
19. Chat names editable (Beta #21)
20. Recent imports max 4 expanded (Beta #23)
21. Sort options above artifacts (Beta #24)
22. Health tab redesign (Beta #25)
23. Auto-generate synopsis on import (Beta #26)
24. Health tab "re-gen all synopses" button (Beta #27)
25. Settings capabilities by tier (Beta #28)
26. Settings editable vs read-only distinction (Beta #30)
27. Pipeline stage tooltips (Beta #31)
28. External data sources consistency (Beta #32)
29. Feedback loop design (Beta #33)
30. Recommended config indicators (Beta #34)
31. Model management UX (Beta #35)
32. Sub-menu formatting audit (Beta #12)
33. Verification premium badge removal (Beta #9)
34. Chat history trash/archive (Beta #13)

---

## PART D: Regression Analysis

Items that appear to have broken during the implementation sprint:

| Issue | Evidence | Likely Cause |
|-------|----------|--------------|
| Selecting previous responses doesn't work (Beta #16) | Not in original punch list | WP8 chat toolbar changes may have affected message selection state |
| Verification stuck on "no claims" even when claims exist (Beta #10) | Not in original punch list | Verification pipeline changes (WP8 cost explainer) may have altered claim extraction trigger |
| Verification "stream interrupted" (Beta #11) | Related to original #27 but the stream failure is new | SSE connection may have been destabilized by verification dashboard additions |

---

## PART E: Build Verification Checklist

Before the next beta test, verify:

- [ ] `./scripts/start-cerid.sh --build` completes without errors
- [ ] All 4 providers detected: `curl localhost:8888/setup/status | jq .configured_providers`
- [ ] PDF ingest + query works: upload 2-page PDF, query within 5 seconds
- [ ] Test buttons respond on API Keys page (all 4 providers)
- [ ] KB card expand/collapse functional
- [ ] Enrichment button visible on chat message bubbles
- [ ] Console LED visible and responsive
- [ ] Verification pipeline shows online in health
- [ ] "Verify last response" extracts claims from a factual response
- [ ] DOCX import succeeds with clear error on failure
- [ ] No `[Errno 35]` in sync status

---

*Generated 2026-04-04 from git log analysis (`54cf9f4..dc8ded3`) cross-referenced with beta test findings.*
