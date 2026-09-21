// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F361 — the invariants card and the status bar both keyed a react-query
 * entry ``["health"]`` while fetching different endpoints. React Query caches
 * on the key alone, so whichever mounted first won and the card read a
 * /health/status payload that carries no ``invariants`` block: every row
 * rendered Unknown / — / 0 no matter what the backend reported.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { StatusBar } from "@/components/layout/status-bar"
import { InvariantsCard } from "@/components/monitoring/invariants-card"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const HEALTH_STATUS = {
  status: "healthy",
  services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
  pipeline_providers: { chat_generation: "quenchforge" },
}

const HEALTH_FULL = {
  status: "healthy",
  services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
  invariants: {
    healthy_invariants: false,
    nli_model_loaded: false,
    verification_report_orphans: 3,
    memory_consolidation_failures_last_24h: 7,
    swallowed_errors_last_hour: { "app.routers.chat": 4 },
  },
}

let urls: string[] = []

function stubBothHealthEndpoints() {
  urls = []
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(url)
      const body = url.includes("/health/status") ? HEALTH_STATUS : HEALTH_FULL
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    }),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("InvariantsCard alongside StatusBar", () => {
  it("fires its own GET /health instead of reusing the status bar's /health/status", async () => {
    stubBothHealthEndpoints()
    render(
      <>
        <StatusBar />
        <InvariantsCard />
      </>,
      { wrapper },
    )
    await waitFor(() => {
      expect(urls.some((u) => u.endsWith("/health"))).toBe(true)
      expect(urls.some((u) => u.includes("/health/status"))).toBe(true)
    })
  })

  it("renders the real invariant values rather than Unknown / 0", async () => {
    stubBothHealthEndpoints()
    render(
      <>
        <StatusBar />
        <InvariantsCard />
      </>,
      { wrapper },
    )
    expect(await screen.findByText("unloaded")).toBeInTheDocument()
    expect(await screen.findByText("3")).toBeInTheDocument()
    expect(await screen.findByText("7")).toBeInTheDocument()
    expect(screen.queryByText("Unknown")).not.toBeInTheDocument()
  })

  it("says the backend reported no invariants rather than rendering zeros", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ status: "healthy", services: {} }),
        }),
      ),
    )
    render(<InvariantsCard />, { wrapper })
    expect(await screen.findByText(/no invariants/i)).toBeInTheDocument()
    // A blank/zero card is indistinguishable from a healthy one — the whole
    // defect. Nothing may claim a count the payload did not carry.
    expect(screen.queryByText("Swallowed errors (last hour)")).not.toBeInTheDocument()
  })

  it("renders a distinct error state when /health itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) })),
    )
    render(<InvariantsCard />, { wrapper })
    // Rendering null on error is pixel-identical to a pane that has no data.
    expect(await screen.findByRole("alert")).toBeInTheDocument()
  })
})
