// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { test, expect, suppressFirstRun } from "./fixtures"

const VIEWPORTS = [
  { name: "phone",  width: 375,  height: 667 },
  { name: "tablet", width: 768,  height: 1024 },
  { name: "laptop", width: 1280, height: 800 },
] as const

test.describe("E-14 responsive layout", () => {
  test.beforeEach(async ({ page }) => {
    // Force the SetupWizard's backend gate off — the responsive layout
    // tests need to reach the main app shell, not the wizard. The
    // sessionStorage / localStorage suppressors only cover the
    // first-run client-side gates; the setup_required server flag
    // requires endpoint-level mocking.
    await page.route("**/setup/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ setup_required: false }),
      })
    })
  })

  for (const vp of VIEWPORTS) {
    test.describe(`${vp.name} ${vp.width}x${vp.height}`, () => {
      test.use({ viewport: { width: vp.width, height: vp.height } })

      test("no horizontal page overflow", async ({ page }) => {
        await suppressFirstRun(page)
        await page.goto("/")
        const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
        const clientWidth = await page.evaluate(() => document.documentElement.clientWidth)
        expect(scrollWidth).toBeLessThanOrEqual(clientWidth)
      })

      test("navigation is accessible", async ({ page }) => {
        await suppressFirstRun(page)
        await page.goto("/")
        if (vp.width < 768) {
          await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible()
          await page.getByRole("button", { name: "Open navigation" }).click()
          // Task 3.7 added a persistent <md bottom tab bar, which is also a
          // labelled `<nav aria-label="Primary">` landmark — scope this
          // assertion to the sidebar's own nav (aria-label="Navigation")
          // opened by the Sheet, to keep the two landmarks disambiguated.
          await expect(page.getByRole("navigation", { name: "Navigation" })).toBeVisible()
        } else {
          await expect(page.getByRole("navigation", { name: "Navigation" })).toBeVisible()
        }
      })

      test("banner fits within viewport", async ({ page }) => {
        await page.addInitScript(() => {
          window.localStorage.removeItem("cerid-model-download-banner-dismissed")
        })
        await suppressFirstRun(page)
        await page.goto("/")
        const banner = page.getByRole("alert")
        const count = await banner.count()
        if (count > 0) {
          const box = await banner.boundingBox()
          if (box) {
            expect(box.width).toBeLessThanOrEqual(vp.width)
            if (vp.width < 768) {
              expect(box.height).toBeLessThanOrEqual(60)
            }
          }
        }
      })
    })
  }
})
