// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared test setup for the Cerid beta-test E2E suite.
 *
 * Re-exports Playwright's default `test` + `expect`. UI tests should
 * call `await suppressFirstRun(page)` immediately after each `page.goto`
 * so the OpeningSequence and the SetupWizard don't gate every test on
 * fixed timeouts.
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
  await page.evaluate(() => {
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
  // After flipping the gates we need to re-render the app so the gates
  // take effect. Reload picks up the new flag state.
  await page.reload()
}
