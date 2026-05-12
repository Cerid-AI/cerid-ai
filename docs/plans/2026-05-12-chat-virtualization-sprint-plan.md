# Chat Message Virtualization — Named Sprint Plan

**Repo:** `/Users/sunrunner/Develop/cerid-ai-internal`
**Target version:** v0.94.0 (next minor — virtualization is a load-bearing UX change)
**Status:** **DEFERRED to named sprint.** Scoping work complete; implementation requires 3–5 contiguous days.

---

## Why this is a named sprint, not an inline pass

The 2026-05-12 scoping pass concluded that this work is **not safe to half-ship.** The streaming auto-scroll integration is load-bearing — break it once and the chat pane silently freezes mid-stream until the user manually scrolls. Better to dedicate 3–5 contiguous days with full test rewrite + feature flag + beta release than to attempt it across an unrelated work stream.

The original v0.84.0 deferral note in `tasks/todo.md` flagged this as "Risk: high." That assessment still holds.

---

## Context

`src/web/src/components/chat/chat-messages.tsx` (288 lines) renders the entire conversation feed with a plain `.map()` over the messages array. At ~20 messages the cost is invisible; at ~200+ (which is realistic for a multi-week thread or any long verification-heavy conversation) we start paying:

- React reconciliation cost on every streaming token (each chunk re-renders the whole list)
- DOM node count growth blocks the GPU compositor
- The Radix `<ScrollArea>` viewport's `scrollHeight` calculation becomes O(N) per scroll event

Production users have not yet flagged this; the work is preemptive. But the v0.84.0 deferral note and this scoping pass converge: ship before users feel pain, not after.

---

## Locked design decisions

1. **Library:** `@tanstack/react-virtual` (~5 KB gzipped, well-maintained, same author as react-query which is already in the bundle).
2. **Anchor strategy:** track the **last message DOM node** as the scroll anchor rather than pixel-counting `scrollHeight`. Virtualizers make `scrollHeight` unreliable, but `data-index="<n>"` markers + `IntersectionObserver` give a stable anchor across re-virtualization.
3. **Feature flag:** `ENABLE_CHAT_VIRTUALIZATION` env var + a runtime localStorage override (`cerid:chat-virtualized=false`) so users can fall back if anything regresses. Default OFF for one release, flip ON in v0.95.0 once the gate has burned in.
4. **jsdom shim:** custom `measureElement` polyfill alongside the existing `ResizeObserver` shim in `src/web/src/__tests__/setup.ts`. Mirror the upstream `@tanstack/react-virtual` recommendation.
5. **Streaming preservation:** the current `distanceFromBottom > SCROLL_ANCHOR_THRESHOLD` check (chat-messages.tsx:120) must keep working — refactor it to consult the virtualizer's `getTotalSize()` rather than `viewport.scrollHeight`.
6. **Beta rollout:** ship the flag default-OFF in v0.94.0, surface as a recommendation in the adaptive recommender engine when message count exceeds 200, flip default-ON in v0.95.0 after one release of soak time.

---

## Files to create / modify

### New frontend

| Path | Purpose |
|---|---|
| `src/web/src/components/chat/virtualized-messages.tsx` | The new virtualized chat list. Mirrors the `<ChatMessages>` shape exactly so the swap is a flag check, not a parent-component refactor. |
| `src/web/src/lib/test/measure-element-shim.ts` | jsdom polyfill for `Element.prototype.getBoundingClientRect` returning realistic non-zero box dimensions when `data-index` is set on the element. Documented thoroughly because every test author needs to understand WHEN it kicks in. |
| `src/web/src/__tests__/virtualized-messages.test.tsx` | Render tests, scroll-anchor tests, feature-flag fallback tests. Target: 15-20 cases. |

### Modified frontend

| Path | Change |
|---|---|
| `package.json` | Add `@tanstack/react-virtual` dependency. |
| `src/web/src/components/chat/chat-messages.tsx` | Read flag from `useChatVirtualization()` hook. When ON, render `<VirtualizedMessages>` instead. Both implementations behind same public API. |
| `src/web/src/components/chat/claim-overlay.tsx` | Replace direct `getBoundingClientRect()` calls with the `data-index` lookup pattern. |
| `src/web/src/__tests__/setup.ts` | Mount the `measureElement` shim alongside `ResizeObserver` polyfill. |
| `src/web/src/__tests__/claim-overlay.test.tsx` | Migrate from manual `document.body.appendChild` workaround to the new shim. |
| `src/mcp/core/config/recommendations.py` | Add a new `RecommendationSpec` entry that surfaces the virtualization flag once a conversation has > 200 messages. **Net-new use case for the C3.2 recommender engine** — exactly what it was built for. |

### New tests (rough scope — the 46 number from v0.84.0)

Per the scoping pass, only **1 test file** directly references `getBoundingClientRect`. The remaining ~20-30 tests that may need adjustment are those that query messages by text content; they'll keep working but should be migrated to `data-index` selectors as a hygiene pass.

---

## Build order

### Phase 1 — Infrastructure (~6-8 hrs, low risk)

1. Add `@tanstack/react-virtual` to `package.json`; run `npm install`.
2. Write the `measure-element-shim.ts` polyfill. Mirror the existing `ResizeObserver` shim shape. Document the contract (what `getBoundingClientRect` returns when `data-index` is set vs not).
3. Mount the shim in `setup.ts`.
4. Smoke test: an existing test that uses `getBoundingClientRect` should now get non-zero values.

### Phase 2 — Virtualizer integration (~8-12 hrs, medium risk)

1. Build `<VirtualizedMessages>` mirroring the `<ChatMessages>` props shape. Key wins: `useVirtualizer({ count, getScrollElement, estimateSize, overscan: 5 })`.
2. Integrate with Radix `<ScrollArea>` — the viewport ref is what `getScrollElement` returns. This is the friction point; the Radix wrapper inserts a div between the visible area and the actual scroll element, so you need a stable ref-forwarding pattern.
3. Render each message inside `virtualizer.getVirtualItems().map(...)` with `data-index` on the outer wrapper.
4. Smoke test: render 500 messages, assert only ~10 are in DOM at a time.

### Phase 3 — Auto-scroll + streaming (~4-6 hrs, HIGH risk)

1. Refactor the scroll-anchor logic. Current code (chat-messages.tsx:118-145):
   ```tsx
   const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
   if (distanceFromBottom > 100) return // user scrolled up
   viewport.scrollTop = viewport.scrollHeight
   ```
   New code consults `virtualizer.getTotalSize()` instead of `scrollHeight`, and uses `virtualizer.scrollToIndex(messages.length - 1, { align: "end" })` instead of pixel math.
2. Streaming case: when a new chunk arrives, the last virtual item's measured height changes. Re-trigger `virtualizer.measure()` and re-anchor.
3. **Critical test:** stream a 50-message conversation, scroll up to message 10, send a new message, verify the view does NOT auto-jump (user override respected).
4. **Critical test:** scroll all the way to bottom, then stream a 100-token response, verify the view follows the streaming caret without jitter.

### Phase 4 — Test rewrites (~8-12 hrs, medium risk)

1. Audit `src/web/src/__tests__/` for any test that uses `querySelector` or `getByText` to find a specific message bubble. With virtualization, off-screen messages aren't in the DOM.
2. Migrate to `data-index` selectors: `screen.getByTestId('msg-index-3')` instead of `screen.getByText('Hello world')`.
3. Add `await screen.findBy*` polling for tests that scroll then query — the virtualizer has a microtask before rendering.
4. The `claim-overlay.test.tsx` specifically — replace the `document.body.appendChild` workaround with the new shim.

### Phase 5 — Feature flag + beta rollout (~4-6 hrs, low risk)

1. Add `ENABLE_CHAT_VIRTUALIZATION` env var (Vite env, exposed to client). Default `"false"`.
2. Add localStorage override `cerid:chat-virtualized`. Read in `useChatVirtualization()` hook with env as the seed.
3. `chat-messages.tsx` wraps the render in:
   ```tsx
   const virtualized = useChatVirtualization()
   return virtualized
     ? <VirtualizedMessages {...props} />
     : <PlainChatMessages {...props} />
   ```
4. Register the new recommendation in `core/config/recommendations.py`:
   ```python
   RecommendationSpec(
     id="chat_virtualization",
     label="Virtualized chat (long conversations)",
     flag_env_var="ENABLE_CHAT_VIRTUALIZATION",
     reason_template="Your longest conversation has {count} messages. Virtualization keeps the chat pane fast at this size.",
     ...
   )
   ```
   This requires a new corpus stat — message-count, not artifact-count. Extend `CorpusStats` to add `max_conversation_length` and have the recommender job query Neo4j for it.

### Phase 6 — Doc cascade + ship (~1-2 hrs)

- Plan doc → this file
- Add to `tasks/todo.md` as active driver
- CHANGELOG entry under v0.94.0
- `docs/ARCHITECTURE.md` short section on virtualization + how the anchor strategy avoids scrollHeight reliance

---

## Test strategy

| Layer | Files | Cases |
|---|---|---|
| Polyfill | `lib/test/measure-element-shim.ts` (unit) | 3 |
| Virtualizer | `__tests__/virtualized-messages.test.tsx` | 12-15 |
| Auto-scroll | `__tests__/virtualized-messages.test.tsx` (sub-suite) | 5-7 |
| Recommender | `mcp/tests/test_config_recommender_job.py` (extend) | 2 |
| Migrated | `__tests__/claim-overlay.test.tsx` | unchanged count, just refactored |

**Hard requirement:** before flipping the default in v0.95.0, the e2e Playwright suite (if/when it exists — currently only vitest) must include a "300-message conversation with mid-stream interruption" scenario.

---

## Verification gates (run before each phase commit)

- `cd src/web && npx tsc -b` (use `-b` for build mode — caught regressions tsc `--noEmit` missed in v0.93.3)
- `cd src/web && npx eslint . --quiet`
- `cd src/web && npx vitest run`
- `cd src/web && npx vite build` — bundle stays under 800KB cap (the cap was added in v0.92.1)
- After phase 3: manual scroll test in a real browser. Use 100+ message fixture.

---

## Risks + mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| `@tanstack/react-virtual` doesn't integrate cleanly with Radix `<ScrollArea>` | Medium | Phase 2 step 2 is the canary. If the ref-forwarding pattern doesn't work cleanly, fall back to a `<div>` scroll element and lose Radix's a11y treatment. Document the trade. |
| Streaming anchor logic breaks in a way unit tests miss | Medium-High | Phase 3 step 3-4 is dedicated to this. ALSO require a manual browser test. ALSO ship default-OFF behind a flag so we can revert without a release. |
| `data-index` migration misses tests that pass against memoized DOM state | Medium | Run the full vitest suite after EACH test file migration, not at the end. Catches regressions while context is fresh. |
| Bundle size regresses past the 800KB cap | Low | `@tanstack/react-virtual` is ~5KB gzipped. If we somehow hit the cap, the existing CI gate fails and we know about it. |
| Users with virtualization off in production never get the benefit | Low | The recommender engine surfaces it once conversation length crosses 200 messages. Users see the banner, click "Enable now", flag flips on. Same pattern as sparse retrieval. |

---

## Out of scope (this sprint)

- Virtualized **artifact lists** in the Knowledge Browser pane. Same problem exists there but it's a separate pane and a separate sprint.
- Virtualized **graph visualizations** (GraphExplorer). The graph uses canvas, not DOM nodes, so virtualization doesn't apply.
- An e2e Playwright suite. The vitest coverage is good enough for v0.94.0 ship; Playwright is its own infrastructure investment.

---

## Sign-off checklist

Before v0.94.0 ships:

- [ ] All 6 phases complete
- [ ] `cd src/web && npx vitest run` — 1088+ pass
- [ ] Manual browser test: 300-message conversation, scroll, stream, send new message at top, send new message at bottom — all interactions feel native
- [ ] `npx vite build` under 800KB
- [ ] Recommender entry merged into `core/config/recommendations.py`
- [ ] CHANGELOG entry written
- [ ] Plan doc committed at `docs/plans/2026-05-12-chat-virtualization-sprint-plan.md` (this file)

Before v0.95.0 flips the default:

- [ ] One full release of soak time with the flag default-OFF
- [ ] Zero open issues mentioning "chat scroll" or "message virtualization" in the GitHub tracker
- [ ] Adaptive recommender has surfaced the toggle to at least one operator (logged in Redis), and they've enabled it without reporting regressions

---

## Why a recommender entry, not a plain default

The adaptive recommendation engine shipped in v0.93.3 was built precisely for this: a feature that's right for some users (long conversations) and pure-cost for others (default install with a single chat). The recommender's three-action banner gives operators agency — "Maybe later" snoozes for the session; "Dismiss permanently" stops asking; "Enable now" flips the toggle. Same pattern, new domain (message count instead of artifact count).

This is the second user of the recommender engine after SPLADE-v3 — confirming the engine's reusability claim.
