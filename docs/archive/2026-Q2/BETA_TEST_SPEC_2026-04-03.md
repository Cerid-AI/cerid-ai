# Cerid AI — Beta Test Punch List & Feature Spec

**Date:** 2026-04-03
**Tester:** Justin (Cerid AI founder)
**Environment:** Mac Mini M1 16GB, Docker Desktop, cerid-ai-public repo
**Build:** v0.81 (post-audit, post-sync)

---

## Part 1: Consolidated Punch List (50 items)

### A. Setup Wizard — Welcome (3)
1. Remove Ollama from welcome system check → move to Optional Features card
2. Memory detection edge cases (HOST_MEMORY_GB — verify on various configs)
3. Docker install guide — verify renders correctly outside Docker

### B. Setup Wizard — API Keys (7)
4. **[HIGH]** Fix Test buttons — hit backend validation, show spinner → success/error
5. **[HIGH]** Add multi-provider / custom LLM option with URL + key config
6. Add OpenRouter "Add Credits" link with "OpenRouter pricing, not Cerid" messaging
7. Add usage rate explainer (costs vary by model/provider)
8. Remove Ollama info box (not actionable on Keys page)
9. Fix Review & Apply showing "Not configured" for keys that exist in .env
10. End-to-end key detection consistency across all wizard pages

### C. Setup Wizard — Structure & Flow (8)
11. **[HIGH]** Remove Domains card — domains grow organically from KB
12. **[HIGH]** Create Optional Features wizard card (Ollama, external APIs, optional configs)
13. Remove/hide Bifrost from wizard entirely
14. Reframe "Simple" mode → "Clean & Simple"
15. Mode card: reflect all configured providers and actual KB state
16. Move optional services note to Optional Features card
17. Service Health: plain language tooltips for each service
18. Service Health: actionable fix/configure buttons for issues

### D. Setup Wizard — Try It Out (3)
19. **[CRITICAL]** Fix PDF drag-drop (activates Adobe Acrobat instead of wizard)
20. **[CRITICAL]** Fix PDF query failure (ingestion succeeds but query fails)
21. **[HIGH]** Optimize ingestion to <5s for 2-page PDF (currently ~60s)

### E. Main GUI — Chat (10)
22. **[HIGH]** Add tooltips to all chat toolbar feedback mode icons
23. **[HIGH]** Explain injection threshold and auto-inject with tooltips
24. **[HIGH]** Explain RAG modes with plain language descriptions
25. Consistent markup/rendering across all panels (not just verification)
26. Expert mode: show estimated cost per verification (~$0.003) instead of scary warning
27. Add way to activate response verification dashboard
28. Privacy mode: visual escalation hierarchy (green → yellow → red) with tooltips
29. Knowledge console: fix duplicate mode selector (keep one interactive)
30. Knowledge console: consistent settings button across panels
31. Add external enrichment button on chat bubbles

### F. Main GUI — Display Issues (3)
32. **[HIGH]** Remove developer tier switch from public build
33. Explain "tokens remaining" context in metrics dashboard
34. Add console activity LED (flashing indicator on new activity)

### G. Knowledge Base Tab (10)
35. **[HIGH]** Fix KB quality scoring (resume scores Q20, should be higher)
36. **[HIGH]** Fix "Preview content" failure
37. Fix plus icon meaning (looks like expand, actually adds to conversation)
38. Add tooltip to chunk count badge ("4 chunks = 4 searchable segments")
39. Make KB cards expandable (~2x height with more content/stats)
40. Make Upload/Import/Duplicates more visually prominent
41. Add descriptive tooltips to all interactive elements
42. Fix external search (returns no results); add manual API routing
43. Add "Add custom API" option in Knowledge Console External section
44. External section: default expanded, sub-categories collapsed

### H. Settings Page (6)
45. **[HIGH]** Fix three LLM providers showing offline despite keys in .env
46. Add info icons explaining chunk size/overlap purpose and recommended values
47. Add tooltips to all non-obvious settings across all tabs
48. Fix inconsistent card expand/collapse defaults
49. Show domain tags on API mouseover in Knowledge Console
50. Fix click affordance — only interactive elements should look clickable

---

## Part 2: Feature Spec

### Problem Statement
50 usability issues from beta testing create a confusing first-run experience and ongoing friction. New users encounter non-functional buttons, unexplained features, misleading indicators, and broken flows.

### Goals
1. Zero broken flows — every wizard step completes without errors
2. Self-explanatory UI — first successful query within 5 minutes, no docs needed
3. Accurate system state — all indicators reflect actual state
4. Sub-5-second wizard ingestion for typical documents
5. Consistent design language across all pages

### Non-Goals
1. Mobile responsive redesign (desktop-first for beta)
2. Bifrost integration (removing from wizard)
3. Custom RAG pipeline builder (post-beta)
4. Multi-user features (enterprise tier)
5. Billing integration (OpenRouter handles payments)

### Phasing

**Phase 1 — P0 (this sprint, ~2-3 days):**
Items 19-21 (Try It Out fixes), 4 (Test buttons), 32 (dev switch), 45 (provider detection), 9-10 (Review & Apply), 11 (remove Domains), 35-36 (KB fixes), Memories/Agents rendering

**Phase 2 — P1 (next sprint, ~4-5 days):**
Items 12 (Optional Features card), 5 (custom LLM), 6-7 (credits/pricing), 14-18 (wizard flow), 22-28 (chat tooltips), 37-44 (KB improvements), 46-50 (settings tooltips)

**Phase 3 — P2 (backlog):**
Items 31 (enrichment button), 34 (activity LED), 42-43 (custom API wizard), 29-30 (panel consistency)

### Open Questions
- Should Bifrost be removed entirely or just hidden from non-advanced users?
- What cost-per-verification estimate to show? Need real data.
- Should custom API wizard support OAuth or just API key auth?
- What quality scoring algorithm should replace current one for KB docs?
- Should external API results auto-enrich KB or require user confirmation?
