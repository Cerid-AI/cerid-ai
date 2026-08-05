// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

  // CR-069: Bifrost was retired 2026-04-17 — the OpenRouter failure tooltips
  // must not promise a fallback gateway that no longer exists.
  it("CR-069: OpenRouter auth-error tooltip does not promise a Bifrost fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...mockHealthy, openrouter_auth_ok: false }),
        }),
      ),
    )
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    const badge = await screen.findByText(/OpenRouter: Auth Error/i)
    await user.hover(badge)
    await waitFor(() => expect(screen.getAllByText(/no fallback gateway/i).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/Bifrost/i)).toHaveLength(0)
  })

  it("CR-069: OpenRouter circuit-open tooltip does not promise a Bifrost fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockHealthy,
              openrouter_auth_ok: true,
              circuit_breakers: { openrouter: "open" },
            }),
        }),
      ),
    )
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    const badge = await screen.findByText(/OpenRouter: Circuit Open/i)
    await user.hover(badge)
    await waitFor(() => expect(screen.getAllByText(/circuit resets/i).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/Bifrost/i)).toHaveLength(0)
  })

  // CH-CREDITS: a recovered "ok" credits status must not render the stale
  // "Credits exhausted" footer.
  it("does not show 'Credits exhausted' when provider credits status is ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = url.includes("/credits") || url.includes("provider")
          ? { configured: true, provider: "openrouter", balance: 39.84, status: "ok" }
          : mockHealthy
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("$39.84")).toBeInTheDocument()
    expect(screen.queryByText("Credits exhausted")).not.toBeInTheDocument()
  })
})
