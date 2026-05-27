// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared test setup for the Cerid beta-test E2E suite.
 *
 * Re-exports Playwright's default `test` + `expect`. UI tests should
 * call `await suppressFirstRun(page)` BEFORE the first `page.goto` so
 * the OpeningSequence and the SetupWizard don't gate every test on
 * fixed timeouts. The function installs an init script that runs in
 * every new document context before any app code executes — that way
 * the gates are flipped on the first read, no reload needed.
 *
 * The two suppressed surfaces:
 *   * OpeningSequence — gated by `sessionStorage["cerid:opening-sequence-played"]`
 *   * SetupWizard onboarding — gated by `localStorage["cerid-onboarding-complete"]`
 *
 * The actual backend `setupRequired` flag still surfaces if the stack
 * isn't configured (no API keys) — we don't suppress that.
 */
import { test, expect, type Page } from "@playwright/test"

export { test, expect }

export async function suppressFirstRun(page: Page): Promise<void> {
  // addInitScript runs in every new document context (including the
  // first navigation) BEFORE any app script. Using page.evaluate()
  // post-navigate raced the React tree's initial render and threw
  // "Execution context was destroyed" mid-nav on slow CI runners.
  await page.addInitScript(() => {
    try {
      sessionStorage.setItem("cerid:opening-sequence-played", "1")
    } catch {
      /* private mode — fine */
    }
    try {
      localStorage.setItem("cerid-onboarding-complete", "1")
    } catch {
      /* same */
    }
  })
}
