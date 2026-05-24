// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-02 — Chat streaming end-to-end.
 *
 * Opens a new conversation from the empty-state CTA, types a turn into
 * the chat composer, sends, observes the assistant bubble populate.
 *
 * Composer is aria-label="Chat message input"; the send affordance is
 * aria-label="Send message".
 *
 * Covers: Phase 3 (chat streaming).
 */
test("E-02 chat streaming end-to-end", async ({ page }) => {
  test.setTimeout(60_000)
  await page.goto("/")
  await suppressFirstRun(page)

  // Empty state surfaces a "New Conversation" CTA in the main pane
  // (uid 5_30 from the live DOM snapshot). The sidebar's "+" button is
  // an alternative path; both end up at the same composer.
  const cta = page.getByRole("button", { name: /^new conversation$/i }).first()
  await cta.click()

  const composer = page.getByRole("textbox", { name: "Chat message input" })
  await expect(composer).toBeVisible({ timeout: 10_000 })

  // Pick a prompt whose expected response token doesn't echo the
  // prompt — avoids strict-mode collisions between the user bubble
  // and the assistant bubble for the same string.
  await composer.fill("What is the capital of France? Answer in one word.")

  const send = page.getByRole("button", { name: "Send message" })
  await expect(send).toBeEnabled({ timeout: 5_000 })
  await send.click()

  // Once the response lands the Send button re-enables and the
  // post-response action buttons ("Copy to clipboard",
  // "Re-verify this response", "Good response", etc.) appear.
  // Probing for "Re-verify this response" is the cleanest signal a
  // full assistant message is rendered without coupling to the
  // markdown-renderer's internal DOM.
  await expect(
    page.getByRole("button", { name: /re-verify this response/i }),
  ).toBeVisible({ timeout: 60_000 })
})
