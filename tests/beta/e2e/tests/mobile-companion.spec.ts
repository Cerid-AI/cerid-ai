// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * Task 3.7 — Mobile companion loop: chat + quick capture reachable on a
 * phone-sized viewport via the <md bottom tab bar (Chat / Capture / Menu).
 *
 * Composer is aria-label="Chat message input"; the send affordance is
 * aria-label="Send message" (see E-02). The bottom tab bar is a labelled
 * `<nav aria-label="Primary">` landmark with three buttons named exactly
 * "Chat" / "Capture" / "Menu".
 *
 * Covers: Phase 3 Task 3.7 (mobile-minimum retrofit).
 */
test.describe("mobile companion (390x844)", () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    // Same backend-gate override as E-14 — reach the app shell, not the
    // SetupWizard.
    await page.route("**/setup/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ setup_required: false }),
      })
    })
  })

  test("bottom tab bar exposes Chat, Capture, and Menu", async ({ page }) => {
    await suppressFirstRun(page)
    await page.goto("/")

    const nav = page.getByRole("navigation", { name: "Primary" })
    await expect(nav).toBeVisible()
    await expect(nav.getByRole("button", { name: "Chat" })).toBeVisible()
    await expect(nav.getByRole("button", { name: "Capture" })).toBeVisible()
    await expect(nav.getByRole("button", { name: "Menu" })).toBeVisible()
  })

  test("send a chat message on a phone viewport", async ({ page }) => {
    test.setTimeout(60_000)
    await suppressFirstRun(page)
    await page.goto("/")

    // Empty state surfaces a "New Conversation" CTA (see E-02).
    const cta = page.getByRole("button", { name: /^new conversation$/i }).first()
    if (await cta.isVisible().catch(() => false)) {
      await cta.click()
    }

    const composer = page.getByRole("textbox", { name: "Chat message input" })
    await expect(composer).toBeVisible({ timeout: 10_000 })
    await composer.fill("What is the capital of France? Answer in one word.")

    const send = page.getByRole("button", { name: "Send message" })
    await expect(send).toBeEnabled({ timeout: 5_000 })
    await send.click()

    // Post-response action buttons appear once a full assistant message
    // renders — same signal E-02 uses.
    await expect(
      page.getByRole("button", { name: /re-verify this response/i }),
    ).toBeVisible({ timeout: 60_000 })

    // The bottom tab bar's Chat tab is the active tab throughout.
    const chatTab = page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Chat" })
    await expect(chatTab).toHaveAttribute("aria-current", "page")
  })

  test("tap Capture in the bottom bar and save a note", async ({ page }) => {
    // Note save rides the synchronous /upload ingest (~11s idle, longer
    // under suite load) — same slow-ingest allowance as F13/E-04.
    test.setTimeout(90_000)
    await suppressFirstRun(page)
    await page.goto("/")

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Capture" })
      .click()

    const dialog = page.getByRole("dialog", { name: /quick capture/i })
    await expect(dialog).toBeVisible()

    const note = `e2e-mobile-companion-${Date.now()}`
    await page.getByLabel("Note content").fill(note)
    await page.getByRole("button", { name: /save note/i }).click()

    await expect(page.getByText("Note saved")).toBeVisible({ timeout: 60_000 })
  })

  test("tap Menu in the bottom bar opens the sidebar navigation", async ({ page }) => {
    await suppressFirstRun(page)
    await page.goto("/")

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Menu" })
      .click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("navigation", { name: "Navigation" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Subjects" })).toBeVisible()
  })
})
