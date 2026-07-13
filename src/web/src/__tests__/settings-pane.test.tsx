// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings shell (SEXTANT) — state matrix, sidebar IA, U-1 mode toggle,
 * search, deep links, and the legacy tab redirect map.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import SettingsPane from "@/components/settings/settings-pane"
import { AdvancedDisclosure } from "@/components/settings/settings-primitives"

vi.mock("@/components/settings/diagnostics-section", () => ({
  DiagnosticsSection: ({ initialTab }: { initialTab: string }) => (
    <div data-testid="diagnostics-console">diagnostics:{initialTab}</div>
  ),
}))

vi.mock("@/components/settings/analytics-section", () => ({
  AnalyticsSection: ({ tier }: { tier: string }) => (
    <div data-testid="analytics-console">analytics:{tier}</div>
  ),
}))

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockSettings = {
  feature_tier: "community",
  feature_flags: {},
  enable_hallucination_check: true,
  enable_feedback_loop: false,
  enable_memory_extraction: false,
  storage_mode: "extract_only",
  machine_id: "test-machine",
  version: "1.0.0",
}

function mockFetch(settingsData: unknown = mockSettings) {
  return vi.fn().mockImplementation((url: string) => {
    const u = typeof url === "string" ? url : ""
    if (u.includes("/billing/capabilities")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ tier: "community", features: {}, buckets: {} }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (u.includes("/health")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "healthy", services: {}, recommended_features: [] }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (u.includes("/providers/credits")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ balance: null, limit: null, used: null }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (u.includes("/settings")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(settingsData),
        text: () => Promise.resolve(JSON.stringify(settingsData)),
      })
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve("{}"),
    })
  })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState({}, "", "/")
  Element.prototype.scrollIntoView = vi.fn()
})

describe("SettingsPane shell — state matrix", () => {
  it("loading: renders skeleton group cards", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<SettingsPane />, { wrapper })
    expect(screen.getByTestId("settings-loading")).toBeInTheDocument()
  })

  it("error: renders destructive alert with working retry", async () => {
    const failing = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/settings")) {
        return Promise.reject(new Error("boom"))
      }
      return mockFetch()(url)
    })
    vi.stubGlobal("fetch", failing)
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByText("boom")).toBeInTheDocument()

    vi.stubGlobal("fetch", mockFetch())
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }))
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    expect(within(nav).getByRole("button", { name: /Models/ })).toBeInTheDocument()
  })

  it("success: renders all 8 categories plus the Diagnostics console entry", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    for (const label of [
      "Models",
      "Knowledge",
      "Retrieval & Answers",
      "Privacy",
      "Extensions",
      "Appearance",
      "Plan & Billing",
      "System",
      "Analytics",
      "Diagnostics",
    ]) {
      expect(within(nav).getByRole("button", { name: new RegExp(label) })).toBeInTheDocument()
    }
  })

  it("Analytics sidebar entry selects the promoted Analytics section (ST9)", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    await userEvent.click(within(nav).getByRole("button", { name: /Analytics/ }))
    expect(await screen.findByTestId("analytics-console")).toBeInTheDocument()
    expect(localStorage.getItem("cerid-settings-category")).toBe("analytics")
  })

  it("defaults to the Overview tab with elevated search and the explore map", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    expect(within(nav).getByRole("button", { name: /Overview/ })).toHaveAttribute(
      "aria-current",
      "page",
    )
    expect(screen.getByRole("searchbox", { name: "Search all settings" })).toBeInTheDocument()
    expect(await screen.findByRole("navigation", { name: /explore settings/i })).toBeInTheDocument()
  })

  it("Overview map rows click through to the owning category page", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const map = await screen.findByRole("navigation", { name: /explore settings/i })

    await userEvent.click(within(map).getByTestId("settings-overview-appearance"))
    expect(await screen.findByRole("radiogroup", { name: "Theme" })).toBeInTheDocument()
    expect(localStorage.getItem("cerid-settings-category")).toBe("appearance")

    const sidebar = screen.getByRole("navigation", { name: "Settings categories" })
    expect(within(sidebar).getByRole("button", { name: /Appearance/ })).toHaveAttribute(
      "aria-current",
      "page",
    )
  })

  it("Overview map reaches the Diagnostics console entry", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const map = await screen.findByRole("navigation", { name: /explore settings/i })
    await userEvent.click(within(map).getByTestId("settings-overview-diagnostics"))
    expect(await screen.findByTestId("diagnostics-console")).toBeInTheDocument()
    expect(localStorage.getItem("cerid-settings-category")).toBe("diagnostics")
  })

  it("success: is axe-clean (D.3)", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { container } = render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("Appearance category renders its registry-driven rows and is axe-clean", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { container } = render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    await userEvent.click(within(nav).getByRole("button", { name: /Appearance/ }))
    expect(await screen.findByRole("radiogroup", { name: "Theme" })).toBeInTheDocument()
    expect(screen.getByRole("radiogroup", { name: "Density" })).toBeInTheDocument()
    expect(screen.getByRole("radiogroup", { name: "Reduce motion" })).toBeInTheDocument()
    expect(localStorage.getItem("cerid-settings-category")).toBe("appearance")
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("SettingsPane shell — U-1 mode toggle", () => {
  it("defaults to Simple and persists flips to cerid-settings-mode", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    const group = screen.getByRole("radiogroup", { name: "Settings detail level" })
    expect(within(group).getByRole("radio", { name: "Simple" })).toHaveAttribute("aria-checked", "true")

    await userEvent.click(within(group).getByRole("radio", { name: "Advanced" }))
    expect(localStorage.getItem("cerid-settings-mode")).toBe("advanced")
    expect(within(group).getByRole("radio", { name: "Advanced" })).toHaveAttribute("aria-checked", "true")
  })

  it("AdvancedDisclosure defaults follow the mode; explicit toggles persist per group", async () => {
    localStorage.setItem("cerid-settings-mode", "simple")
    const { unmount } = render(
      <AdvancedDisclosure category="models" group="testgroup" count={3}>
        <div data-testid="advanced-content" />
      </AdvancedDisclosure>,
    )
    expect(screen.queryByTestId("advanced-content")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Advanced — 3 settings/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    )

    await userEvent.click(screen.getByRole("button", { name: /Advanced — 3 settings/ }))
    expect(screen.getByTestId("advanced-content")).toBeInTheDocument()
    expect(localStorage.getItem("cerid-settings-disclosure:models.testgroup")).toBe("open")
    unmount()

    localStorage.removeItem("cerid-settings-disclosure:models.testgroup")
    localStorage.setItem("cerid-settings-mode", "advanced")
    render(
      <AdvancedDisclosure category="models" group="testgroup" count={3}>
        <div data-testid="advanced-content-2" />
      </AdvancedDisclosure>,
    )
    expect(screen.getByTestId("advanced-content-2")).toBeInTheDocument()
  })
})

describe("SettingsPane shell — search", () => {
  it("shows a flat result list and reveals the clicked setting", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })

    await userEvent.type(screen.getByRole("searchbox", { name: "Search settings" }), "dark mode")
    const results = await screen.findByRole("list", { name: "Search results" })
    const themeResult = within(results).getByRole("listitem")
    expect(themeResult).toHaveTextContent("Appearance › theme")

    await userEvent.click(within(themeResult).getByRole("button"))
    expect(await screen.findByRole("radiogroup", { name: "Theme" })).toBeInTheDocument()
    expect(window.location.search).toContain("setting=appearance.theme.mode")
    expect(screen.getByRole("searchbox", { name: "Search settings" })).toHaveValue("")
  })

  it("renders EmptyState on no match with a clear button", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })

    await userEvent.type(screen.getByRole("searchbox", { name: "Search settings" }), "zzznomatch")
    expect(await screen.findByText(/No settings match/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Clear search" }))
    await waitFor(() => {
      expect(screen.queryByText(/No settings match/)).not.toBeInTheDocument()
    })
  })

  it("'/' focuses the elevated Overview search when on Overview", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })

    await userEvent.keyboard("/")
    expect(screen.getByRole("searchbox", { name: "Search all settings" })).toHaveFocus()
  })

  it("'/' falls back to the sidebar search on a category page", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    await userEvent.click(within(nav).getByRole("button", { name: /Models/ }))

    await userEvent.keyboard("/")
    expect(screen.getByRole("searchbox", { name: "Search settings" })).toHaveFocus()
  })

  it("typing in the elevated Overview search keeps focus and shows results in place", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: /explore settings/i })

    const elevated = screen.getByRole("searchbox", { name: "Search all settings" })
    await userEvent.type(elevated, "dark mode")
    const results = await screen.findByRole("list", { name: "Search results" })
    expect(within(results).getByRole("listitem")).toHaveTextContent("Appearance › theme")
    expect(screen.getByRole("searchbox", { name: "Search all settings" })).toHaveFocus()
    expect(screen.queryByRole("navigation", { name: /explore settings/i })).not.toBeInTheDocument()
  })
})

describe("SettingsPane shell — deep links + redirects", () => {
  it("?setting= lands on the owning category and anchors the row", async () => {
    window.history.replaceState({}, "", "/?setting=appearance.theme.mode")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByRole("radiogroup", { name: "Theme" })).toBeInTheDocument()
    await waitFor(() => {
      expect(document.getElementById("appearance.theme.mode")).toBeInTheDocument()
    })
  })

  it("?diagnostics_tab= lands on the Diagnostics console entry unchanged", async () => {
    window.history.replaceState({}, "", "/?diagnostics_tab=status")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByTestId("diagnostics-console")).toHaveTextContent("diagnostics:status")
  })

  it("legacy ?diagnostics_tab=analytics routes to the promoted Analytics section (ST9)", async () => {
    window.history.replaceState({}, "", "/?diagnostics_tab=analytics")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByTestId("analytics-console")).toBeInTheDocument()
  })

  it("?category=analytics selects the Analytics section", async () => {
    window.history.replaceState({}, "", "/?category=analytics")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByTestId("analytics-console")).toBeInTheDocument()
  })

  it("redirects legacy cerid-settings-tab values through the J-4 map", async () => {
    localStorage.setItem("cerid-settings-tab", "governance")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    await waitFor(() => {
      expect(within(nav).getByRole("button", { name: /Extensions/ })).toHaveAttribute(
        "aria-current",
        "page",
      )
    })
  })

  it("prefers the new cerid-settings-category key over the legacy tab key", async () => {
    localStorage.setItem("cerid-settings-tab", "governance")
    localStorage.setItem("cerid-settings-category", "privacy")
    vi.stubGlobal("fetch", mockFetch())
    render(<SettingsPane />, { wrapper })
    const nav = await screen.findByRole("navigation", { name: "Settings categories" })
    await waitFor(() => {
      expect(within(nav).getByRole("button", { name: /Privacy/ })).toHaveAttribute(
        "aria-current",
        "page",
      )
    })
  })
})
