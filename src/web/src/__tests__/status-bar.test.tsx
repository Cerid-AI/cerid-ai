// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { StatusBar } from "@/components/layout/status-bar"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockHealthy = {
  status: "healthy",
  services: {
    chromadb: "connected",
    redis: "connected",
    neo4j: "connected",
  },
}

const mockDegraded = {
  status: "degraded",
  services: {
    chromadb: "connected",
    redis: "error",
    neo4j: "connected",
  },
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("StatusBar", () => {
  it("renders service names in status text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealthy),
      }),
    )
    render(<StatusBar />, { wrapper })
    // Services render as "{name}: {state}" — e.g., "chromadb: connected"
    expect(await screen.findByText(/chromadb/i)).toBeInTheDocument()
    expect(screen.getByText(/redis/i)).toBeInTheDocument()
    expect(screen.getByText(/neo4j/i)).toBeInTheDocument()
  })

  it("shows healthy status message when all services connected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealthy),
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("All systems operational")).toBeInTheDocument()
  })

  it("shows degraded status message when services have errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockDegraded),
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("Some services degraded")).toBeInTheDocument()
  })
})
