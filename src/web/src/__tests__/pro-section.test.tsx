// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import { axe } from "jest-axe"
import { ProSection } from "@/components/settings/pro-section"
import type { CapabilitiesResponse } from "@/lib/api/billing"

vi.mock("@/lib/api/billing", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/billing")>("@/lib/api/billing")
  return { ...actual, fetchCapabilities: vi.fn() }
})

import { fetchCapabilities } from "@/lib/api/billing"

const mockFetchCapabilities = fetchCapabilities as unknown as ReturnType<typeof vi.fn>

function caps(overrides: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return {
    tier: "community",
    features: {
      custom_smart_rag: { enabled: false, tier_required: "pro" },
      advanced_analytics: { enabled: false, tier_required: "pro" },
      ocr_parsing: { enabled: true, tier_required: "community" },
      multi_user: { enabled: false, tier_required: "enterprise" },
    },
    buckets: {},
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  // The Current Plan / license / waitlist sections use raw fetch on mount.
  // Stub it so those calls resolve benignly during the matrix tests.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ active: false, tier: "community" }),
    }),
  )
})

describe("ProSection — capabilities matrix", () => {
  it("shows loading skeletons while capabilities are pending", () => {
    mockFetchCapabilities.mockReturnValue(new Promise(() => {}))
    render(<ProSection featureTier="community" />, { wrapper })
    expect(screen.getByTestId("pro-features-loading")).toBeInTheDocument()
  })

  it("shows an error alert with retry when capabilities fail", async () => {
    mockFetchCapabilities.mockRejectedValue(new Error("boom"))
    render(<ProSection featureTier="community" />, { wrapper })
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument())
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("renders tier sections from capabilities with live enabled state", async () => {
    mockFetchCapabilities.mockResolvedValue(caps())
    render(<ProSection featureTier="community" />, { wrapper })

    // Pro feature appears and is Locked at community tier.
    await waitFor(() => expect(screen.getByText(/Custom Smart RAG/i)).toBeInTheDocument())
    // Community feature is Enabled.
    expect(screen.getByText(/OCR Text Extraction/i)).toBeInTheDocument()
    // Enterprise feature is surfaced (was dropped if rendered from buckets only).
    expect(screen.getByText(/Multi-User Auth/i)).toBeInTheDocument()
    // Live state: at community tier the pro feature shows Locked, community shows Enabled.
    expect(screen.getAllByText(/Locked/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Enabled/i).length).toBeGreaterThan(0)
  })

  it("reflects Pro-tier enabled state from the API", async () => {
    mockFetchCapabilities.mockResolvedValue(
      caps({
        tier: "pro",
        features: {
          custom_smart_rag: { enabled: true, tier_required: "pro" },
          ocr_parsing: { enabled: true, tier_required: "community" },
        },
      }),
    )
    render(<ProSection featureTier="pro" />, { wrapper })
    await waitFor(() => expect(screen.getByText(/Custom Smart RAG/i)).toBeInTheDocument())
    // No Locked badges when everything shown is enabled.
    expect(screen.queryByText(/Locked/i)).not.toBeInTheDocument()
  })

  it("is axe-clean on the success path", async () => {
    mockFetchCapabilities.mockResolvedValue(caps())
    const { container } = render(<ProSection featureTier="community" />, { wrapper })
    await waitFor(() => expect(screen.getByText(/Custom Smart RAG/i)).toBeInTheDocument())
    expect(await axe(container)).toHaveNoViolations()
  })

  it("retry refetches capabilities", async () => {
    mockFetchCapabilities.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(caps())
    render(<ProSection featureTier="community" />, { wrapper })
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    await waitFor(() => expect(screen.getByText(/Custom Smart RAG/i)).toBeInTheDocument())
    expect(mockFetchCapabilities).toHaveBeenCalledTimes(2)
  })
})
