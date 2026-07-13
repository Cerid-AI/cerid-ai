// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings Overview tab (ST1) — recommendations differentiated, an active
 * (non-default) configuration summary, and the grouped explore map with
 * per-category one-liners, status hints, tier locks, and click-throughs.
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
  vi.clearAllMocks()
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

  it("renders the grouped explore map with a one-line explanation per category", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })

    const nav = await screen.findByRole("navigation", { name: /explore settings/i })
    for (const group of [
      "Models & Providers",
      "Knowledge & Retrieval",
      "Privacy & Data",
      "Connections & Extensions",
      "Preferences & Plan",
      "System & Monitoring",
    ]) {
      expect(within(nav).getByText(group)).toBeInTheDocument()
    }
    // Every category row carries its one-liner.
    expect(
      within(nav).getByRole("button", { name: /Ingest documents, watch folders/i }),
    ).toBeInTheDocument()
    expect(
      within(nav).getByRole("button", { name: /searched, ranked, and verified/i }),
    ).toBeInTheDocument()
    expect(
      within(nav).getByRole("button", { name: /encryption, retention, and what leaves this machine/i }),
    ).toBeInTheDocument()
  })

  it("map rows fire onSelectCategory — including the console entries", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: /explore settings/i })

    await userEvent.click(within(nav).getByTestId("settings-overview-models"))
    expect(baseProps.onSelectCategory).toHaveBeenCalledWith("models")

    await userEvent.click(within(nav).getByTestId("settings-overview-diagnostics"))
    expect(baseProps.onSelectCategory).toHaveBeenCalledWith("diagnostics")
  })

  it("shows the tier-lock badge on categories with entitlement-gated settings", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: /explore settings/i })

    const knowledgeRow = within(nav).getByTestId("settings-overview-knowledge")
    expect(within(knowledgeRow).getByText(/\d+ Pro/)).toBeInTheDocument()
    // Console entries never carry a lock badge.
    const diagnosticsRow = within(nav).getByTestId("settings-overview-diagnostics")
    expect(within(diagnosticsRow).queryByText(/Pro/)).not.toBeInTheDocument()
  })

  it("surfaces status hints from data already loaded — no extra fetches", async () => {
    const settings = {
      feature_tier: "community",
      domains: ["coding", "finance"],
      enable_hallucination_check: true,
      enable_encryption: false,
      mcp_client_mode: "allowlist",
      version: "1.2.3",
    } as never
    render(
      <SettingsOverview
        settings={settings}
        {...baseProps}
        credits={{ configured: true } as never}
      />,
      { wrapper },
    )
    const nav = await screen.findByRole("navigation", { name: /explore settings/i })
    expect(within(nav).getByText("2 domains")).toBeInTheDocument()
    expect(within(nav).getByText("Verification on")).toBeInTheDocument()
    expect(within(nav).getByText("Encryption off")).toBeInTheDocument()
    expect(within(nav).getByText("MCP allowlist")).toBeInTheDocument()
    expect(within(nav).getByText("v1.2.3")).toBeInTheDocument()
    expect(within(nav).getByText("API provider connected")).toBeInTheDocument()
    expect(within(nav).getByText("Community tier")).toBeInTheDocument()
  })

  it("omits status hints whose backing field is absent", async () => {
    const settings = { feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: /explore settings/i })
    expect(within(nav).queryByText(/^\d+ domains?$/)).not.toBeInTheDocument()
    expect(within(nav).queryByText(/^Verification (on|off)$/)).not.toBeInTheDocument()
    expect(within(nav).queryByText("API provider connected")).not.toBeInTheDocument()
    expect(within(nav).queryByText("No API provider configured")).not.toBeInTheDocument()
  })

  it("badges categories that have modified settings on the map", async () => {
    const settings = { auto_inject_threshold: 0.4, feature_tier: "community" } as never
    render(<SettingsOverview settings={settings} {...baseProps} />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: /explore settings/i })
    const retrievalRow = within(nav).getByTestId("settings-overview-retrieval")
    expect(within(retrievalRow).getByText("1 modified")).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const settings = {
      auto_inject_threshold: 0.4,
      feature_tier: "community",
      domains: ["coding"],
      version: "1.2.3",
    } as never
    const { container } = render(
      <SettingsOverview settings={settings} {...baseProps} credits={{ configured: false } as never} />,
      { wrapper },
    )
    await screen.findByRole("navigation", { name: /explore settings/i })
    expect(await axe(container)).toHaveNoViolations()
  })
})
