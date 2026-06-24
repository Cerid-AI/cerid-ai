// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings Overview tab (ST1) — recommendations differentiated, an active
 * (non-default) configuration summary, and a jump-to-category grid.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { SettingsOverview } from "@/components/settings/settings-overview"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "healthy", recommended_features: [] }),
        text: () => Promise.resolve("{}"),
      }),
    ),
  )
}

const baseProps = {
  patch: vi.fn(async () => ({ ok: true as const })),
  tier: "community" as const,
  onRevealSetting: vi.fn(),
  onSelectCategory: vi.fn(),
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  Element.prototype.scrollIntoView = vi.fn()
  stubFetch()
})

describe("SettingsOverview", () => {
  it("lists non-default settings with their current value and links to the row", async () => {
    const settings = { auto_inject_threshold: 0.4, feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })

    const active = await screen.findByRole("region", { name: /active configuration/i })
    const row = within(active).getByRole("button", { name: /Injection threshold/i })
    expect(row).toBeInTheDocument()

    await userEvent.click(row)
    expect(baseProps.onRevealSetting).toHaveBeenCalledWith(
      expect.objectContaining({ id: "retrieval.contextInjection.threshold" }),
    )
  })

  it("shows an at-defaults empty state when nothing is modified", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    expect(await screen.findByText(/at (its|their) recommended defaults/i)).toBeInTheDocument()
  })

  it("renders a jump-to-category grid that fires onSelectCategory", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    const grid = await screen.findByRole("navigation", { name: /jump to a category/i })
    await userEvent.click(within(grid).getByRole("button", { name: /Privacy/ }))
    expect(baseProps.onSelectCategory).toHaveBeenCalledWith("privacy")
  })

  it("is axe-clean", async () => {
    const settings = { auto_inject_threshold: 0.4, feature_tier: "community" } as never
    const { container } = render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    await screen.findByRole("navigation", { name: /jump to a category/i })
    expect(await axe(container)).toHaveNoViolations()
  })
})
