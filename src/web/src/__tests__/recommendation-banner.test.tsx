// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the adaptive RecommendationBanner (SEXTANT rehost).
 *
 * Covers:
 *   - banner renders only when /health includes recommendations
 *   - "Enable now" calls patch() with the rec's enable_payload + clearRecommendation
 *   - Enable failures surface an inline destructive Alert (never silent)
 *   - "Snooze" (labeled button) snoozes the entry via sessionStorage
 *   - the X icon dismisses permanently via the server-side endpoint
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { RecommendationBanner } from "@/components/settings/recommendation-banner"

const SAMPLE_REC = {
  id: "sparse_retrieval",
  label: "SPLADE-v3 sparse retrieval",
  reason: "Your corpus is now 150 documents.",
  triggered_at: "2026-05-12T00:00:00+00:00",
  corpus_size: 150,
  enable_payload: { enable_sparse_retrieval: true, hybrid_fusion_mode: "tri_rrf" },
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

function mockHealthFetch(features: typeof SAMPLE_REC[]) {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    if (typeof url === "string" && url.includes("/health")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({
          status: "healthy",
          services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
          recommended_features: features,
        }),
      })
    }
    return Promise.resolve({
      ok: true,
      status: 204,
      json: () => Promise.resolve({}),
    })
  }) as unknown as typeof fetch
}

describe("RecommendationBanner", () => {
  it("renders nothing when /health has no recommendations", async () => {
    mockHealthFetch([])
    const patch = vi.fn()
    const { container } = render(wrap(<RecommendationBanner patch={patch} />))
    await waitFor(() => {
      expect(container.querySelectorAll('[role="button"]').length).toBe(0)
    })
    expect(container.textContent).toBe("")
  })

  it("renders one card per recommendation entry", async () => {
    mockHealthFetch([SAMPLE_REC])
    const patch = vi.fn()
    render(wrap(<RecommendationBanner patch={patch} />))
    await screen.findByText("SPLADE-v3 sparse retrieval")
    expect(screen.getByText(/Your corpus is now 150 documents/)).toBeInTheDocument()
  })

  it("Enable now calls patch with the rec's enable_payload", async () => {
    mockHealthFetch([SAMPLE_REC])
    const patch = vi.fn().mockResolvedValue({ ok: true })
    render(wrap(<RecommendationBanner patch={patch} />))
    await screen.findByText("Enable now")

    await userEvent.click(screen.getByText("Enable now"))
    await waitFor(() => {
      expect(patch).toHaveBeenCalledWith({
        enable_sparse_retrieval: true,
        hybrid_fusion_mode: "tri_rrf",
      })
    })
  })

  it("surfaces enable failures as an inline alert", async () => {
    mockHealthFetch([SAMPLE_REC])
    const patch = vi.fn().mockResolvedValue({ ok: false, error: "PATCH rejected" })
    render(wrap(<RecommendationBanner patch={patch} />))
    await screen.findByText("Enable now")

    await userEvent.click(screen.getByText("Enable now"))
    expect(await screen.findByText(/Could not enable: PATCH rejected/)).toBeInTheDocument()
    expect(screen.getByText("SPLADE-v3 sparse retrieval")).toBeInTheDocument()
  })

  it("Snooze hides the entry via sessionStorage without a network call", async () => {
    mockHealthFetch([SAMPLE_REC])
    const patch = vi.fn()
    render(wrap(<RecommendationBanner patch={patch} />))
    await screen.findByText("SPLADE-v3 sparse retrieval")

    await userEvent.click(screen.getByRole("button", { name: "Snooze" }))

    await waitFor(() => {
      expect(screen.queryByText("SPLADE-v3 sparse retrieval")).not.toBeInTheDocument()
    })
    expect(sessionStorage.getItem("cerid:recommendation-snoozed:sparse_retrieval")).toBe("1")
  })

  it("the X icon dismisses permanently via the dismiss endpoint", async () => {
    mockHealthFetch([SAMPLE_REC])
    const patch = vi.fn()
    render(wrap(<RecommendationBanner patch={patch} />))
    await screen.findByText("SPLADE-v3 sparse retrieval")

    await userEvent.click(screen.getByLabelText("Dismiss permanently"))

    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      const dismissCall = calls.find(
        (c) => typeof c[0] === "string" && c[0].includes("/settings/recommendations/sparse_retrieval/dismiss"),
      )
      expect(dismissCall).toBeDefined()
    })
  })
})
