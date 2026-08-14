// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"
import { axe } from "jest-axe"

vi.mock("@/lib/api", () => ({
  fetchAutomations: vi.fn(),
  createAutomation: vi.fn(),
  updateAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  toggleAutomation: vi.fn(),
  runAutomation: vi.fn(),
}))
// The dialog's domain picker reads the live domain aggregate (UX-08);
// unavailable here → falls back to the static taxonomy.
vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn(() => Promise.reject(new Error("down"))),
}))

import { fetchAutomations } from "@/lib/api"
import AutomationsPane from "@/components/automations/automations-pane"

// AutomationDialog uses useQuery for the domain list — every render needs
// a QueryClientProvider.
function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const mockAutomations = [
  {
    id: "auto-1",
    name: "Daily Digest",
    description: "Summarizes daily activity",
    prompt: "Summarize today's activity across all domains",
    action: "digest" as const,
    schedule: "0 9 * * *",
    domains: ["coding"],
    enabled: true,
    run_count: 5,
    last_run_at: "2026-03-20T09:00:00Z",
    last_status: "success",
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
]

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("AutomationsPane", () => {
  it("renders New button", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue(mockAutomations)
    render(<AutomationsPane />)
    expect(await screen.findByText("New")).toBeInTheDocument()
  })

  it("renders automation cards after loading", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue(mockAutomations)
    render(<AutomationsPane />)
    expect(await screen.findByText("Daily Digest")).toBeInTheDocument()
  })

  it("shows empty state when no automations", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue([])
    render(<AutomationsPane />)
    await waitFor(() => {
      expect(screen.getByText("No automations yet")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix (loading + error complete the matrix; empty/success
// are covered by the existing tests above)
// ---------------------------------------------------------------------------

describe("AutomationsPane — four-state matrix (D.2)", () => {
  it("loading: shows the loading spinner while fetching, no cards or empty-state yet", () => {
    vi.mocked(fetchAutomations).mockReturnValue(new Promise(() => {})) // never resolves
    render(<AutomationsPane />)
    expect(screen.getByText("Automations")).toBeInTheDocument()
    expect(screen.queryByText("No automations yet")).not.toBeInTheDocument()
    expect(screen.queryByText("Daily Digest")).not.toBeInTheDocument()
  })

  it("error: shows the error message with a Retry button on fetch failure", async () => {
    vi.mocked(fetchAutomations).mockRejectedValue(new Error("Connection refused"))
    render(<AutomationsPane />)
    await waitFor(() => {
      expect(screen.getByText(/Connection refused/i)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("AutomationsPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in loading state", async () => {
    vi.mocked(fetchAutomations).mockReturnValue(new Promise(() => {}))
    const { container } = render(<AutomationsPane />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    vi.mocked(fetchAutomations).mockRejectedValue(new Error("Connection refused"))
    const { container } = render(<AutomationsPane />)
    await waitFor(() => screen.getByText(/Connection refused/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in empty state", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue([])
    const { container } = render(<AutomationsPane />)
    await screen.findByText("No automations yet")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in populated state", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue(mockAutomations)
    const { container } = render(<AutomationsPane />)
    await screen.findByText("Daily Digest")
    expect(await axe(container)).toHaveNoViolations()
  })
})
