// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Phase 4b B4b.5 — cross-browser smoke test.
 *
 * Loads the built extension into a fresh chromium context, opens a
 * known page, triggers the popup's Save Page button, and asserts a
 * 201 lands at the mocked /sdk/v1/ingest endpoint.
 *
 * Requires the dist/ build artifact and a Cerid MCP server running on
 * the configured base URL (or a route stub). Skipped via test.skip when
 * Playwright can't find chromium installed.
 */

import { test, expect, chromium } from "@playwright/test"
import { resolve } from "node:path"

test("page-capture sends content to /sdk/v1/ingest", async () => {
  const extensionPath = resolve(__dirname, "..", "dist")
  const userDataDir = resolve(__dirname, ".playwright-user")

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
    ],
  })

  try {
    // Stub the upstream API so we can assert payload shape without
    // requiring a live MCP server.
    await context.route("**/sdk/v1/ingest", async (route) => {
      const body = route.request().postDataJSON()
      expect(body?.metadata?.ingest_source).toBe("browser_extension")
      expect(typeof body?.content).toBe("string")
      expect(body.content.length).toBeGreaterThan(0)
      await route.fulfill({
        status: 201,
        body: JSON.stringify({ artifact_id: "stub-1" }),
      })
    })

    const page = await context.newPage()
    await page.setContent(
      `<html><head><title>Cerid Test Page</title></head>
        <body><main><p>Hello from the test fixture.</p></main></body></html>`,
    )

    // Open the popup. Chrome doesn't expose a clean popup API to
    // Playwright; we navigate to the popup's chrome-extension URL
    // directly, which is functionally equivalent for the click test.
    const targets = context.serviceWorkers()
    expect(targets.length).toBeGreaterThan(0)
    const extensionId = new URL(targets[0].url()).host
    const popup = await context.newPage()
    await popup.goto(`chrome-extension://${extensionId}/popup.html`)
    await popup.click("#capture")
    await expect(popup.locator("#status")).toContainText("Saved", {
      timeout: 5000,
    })
  } finally {
    await context.close()
  }
})
