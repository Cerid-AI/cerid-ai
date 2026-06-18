// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { test, expect, suppressFirstRun } from "./fixtures"

/**
 * E-06 — Settings pane: SEXTANT shell.
 *
 * Covers the intent-oriented settings redesign:
 *   - 8 intent categories + Diagnostics console entry in the sidebar
 *   - Simple | Advanced mode toggle drives the per-group disclosures
 *   - Registry-driven search reveals a row inside a collapsed expander
 *   - A toggle write round-trips through the server (persists across reload)
 *   - Private Mode renders the canonical L0–L4 contract
 *   - Appearance exposes the theme triad
 */
test("E-06 settings — SEXTANT shell: categories, mode toggle, search reveal, write round-trip", async ({ page }) => {
  test.setTimeout(120_000)
  await suppressFirstRun(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Settings", exact: true }).click()

  // Sidebar: 8 intent categories + Diagnostics console entry.
  const main = page.getByRole("main")
  for (const cat of [
    /^Models/,
    /^Knowledge/,
    /^Retrieval & Answers/,
    /^Privacy/,
    /^Extensions/,
    /^Appearance/,
    /^Plan & Billing/,
    /^System/,
  ]) {
    await expect(main.getByRole("button", { name: cat })).toBeVisible({ timeout: 15_000 })
  }
  await expect(main.getByRole("button", { name: "Diagnostics" })).toBeVisible()

  // U-1: Simple (default) keeps advanced groups collapsed; Advanced opens them.
  await main.getByRole("button", { name: /^Models/ }).click()
  const disclosure = main.getByRole("button", { name: /^Advanced — \d+ setting/ }).first()
  await expect(disclosure).toBeVisible({ timeout: 15_000 })
  const detailLevel = page.getByRole("radiogroup", { name: "Settings detail level" })
  await detailLevel.getByRole("radio", { name: "Advanced" }).click()
  await expect(disclosure).toHaveAttribute("aria-expanded", "true")
  await detailLevel.getByRole("radio", { name: "Simple" }).click()
  await expect(disclosure).toHaveAttribute("aria-expanded", "false")

  // Search: cross-category results reveal a row hidden behind a collapsed
  // expander, deep-linking via ?setting=.
  await main.getByPlaceholder(/Search settings/i).fill("chunk")
  const hit = main.getByRole("button", { name: /Chunk size/ }).first()
  await expect(hit).toBeVisible({ timeout: 10_000 })
  await hit.click()
  await expect(page).toHaveURL(/setting=knowledge\.ingestion\.chunkSize/)
  await expect(main.getByText("CHUNK_MAX_TOKENS").first()).toBeVisible({ timeout: 10_000 })

  // Backend write round-trip: flip Self-RAG off, reload, confirm the server
  // kept it, then restore.
  await main.getByRole("button", { name: /^Retrieval & Answers/ }).click()
  const selfRag = main.getByRole("switch", { name: "Self-RAG validation" })
  await expect(selfRag).toBeVisible({ timeout: 15_000 })
  const initial = await selfRag.getAttribute("aria-checked")
  await selfRag.click()
  const flipped = initial === "true" ? "false" : "true"
  await expect(selfRag).toHaveAttribute("aria-checked", flipped, { timeout: 10_000 })

  await page.reload()
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  const main2 = page.getByRole("main")
  await main2.getByRole("button", { name: /^Retrieval & Answers/ }).click()
  const selfRag2 = main2.getByRole("switch", { name: "Self-RAG validation" })
  await expect(selfRag2).toBeVisible({ timeout: 15_000 })
  await expect(selfRag2).toHaveAttribute("aria-checked", flipped, { timeout: 10_000 })
  await selfRag2.click()
  await expect(selfRag2).toHaveAttribute("aria-checked", initial!, { timeout: 10_000 })

  // Privacy: canonical L0–L4 contract (same scale as the chat toolbar).
  await main2.getByRole("button", { name: /^Privacy/ }).click()
  await expect(main2.getByRole("button", { name: /L1 — Skip saves & sync/ })).toBeVisible({ timeout: 10_000 })
  await expect(main2.getByRole("button", { name: /L4 — Full ephemeral/ })).toBeVisible()

  // Appearance: theme triad present.
  await main2.getByRole("button", { name: /^Appearance/ }).click()
  for (const theme of ["Light", "Dark", "System"]) {
    await expect(main2.getByRole("radio", { name: theme }).or(main2.getByRole("button", { name: theme, exact: true })).first()).toBeVisible({ timeout: 10_000 })
  }
})
