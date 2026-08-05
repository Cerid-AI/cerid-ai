// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

// Mock the API module before importing the component
vi.mock("@/lib/api/trust-score", () => ({
  fetchTrustScore: vi.fn(),
}))

import { fetchTrustScore } from "@/lib/api/trust-score"
import { TrustScoreChip } from "@/components/trust-score"
import type { TrustScore } from "@/lib/types/trust-score"

const mockedFetch = fetchTrustScore as ReturnType<typeof vi.fn>

function renderWithQuery(ui: React.ReactElement, queryClient?: QueryClient) {
  const client =
    queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
    })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const makeScore = (overrides: Partial<TrustScore> = {}): TrustScore => ({
  score: 94,
  band: "high",
  updated_at: "2026-05-10T06:00:00Z",
  components: [
    {
      id: "faithfulness",
      label: "Faithfulness",
      value: 0.93,
      target: 0.9,
      normalized: 1.0,
      status: "ok",
      source: "nightly RAGAS",
      last_updated_at: "2026-05-10T02:00:00Z",
      note: null,
    },
    {
      id: "retrieval_ndcg10",
      label: "Retrieval (NDCG@10)",
      value: 0.87,
      target: 0.85,
      normalized: 1.0,
      status: "ok",
      source: "nightly IR baseline",
      last_updated_at: "2026-05-10T02:00:00Z",
      note: null,
    },
    {
      id: "memory_recall",
      label: "Memory recall (LongMemEval)",
      value: null,
      target: 0.8,
      normalized: null,
      status: "not_available",
      source: "weekly LongMemEval run",
      last_updated_at: null,
      note: "longmemeval.json not yet generated",
    },
    {
      id: "verification_coverage",
      label: "Verification coverage",
      value: 0.97,
      target: 0.95,
      normalized: 1.0,
      status: "ok",
      source: "Neo4j rolling 24h",
      last_updated_at: "2026-05-10T05:45:00Z",
      note: "97/100",
    },
    {
      id: "preservation_health",
      label: "Preservation health",
      value: null,
      target: 1.0,
      normalized: null,
      status: "not_available",
      source: "last main CI",
      last_updated_at: null,
      note: "preservation.json not yet written by CI",
    },
  ],
  ...overrides,
})

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("TrustScoreChip", () => {
  describe("renders chip with score from mocked query", () => {
    it("shows score and label when data loads", async () => {
      mockedFetch.mockResolvedValue(makeScore())
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip).toBeInTheDocument()
      expect(chip.textContent).toMatch(/Trust 94/)
    })
  })

  describe("loading skeleton while fetching", () => {
    it("shows skeleton before data resolves", () => {
      mockedFetch.mockReturnValue(new Promise(() => {})) // never resolves
      renderWithQuery(<TrustScoreChip />)
      expect(screen.getByTestId("trust-score-skeleton")).toBeInTheDocument()
    })
  })

  describe("HoverCard opens on keyboard focus", () => {
    it("chip is keyboard-focusable", async () => {
      mockedFetch.mockResolvedValue(makeScore())
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      chip.focus()
      expect(document.activeElement).toBe(chip)
    })
  })

  describe("Dialog opens on click", () => {
    it("opens modal when chip is clicked", async () => {
      const user = userEvent.setup()
      mockedFetch.mockResolvedValue(makeScore())
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      await user.click(chip)
      // Modal title should include "Cerid Trust Score"
      expect(screen.getByRole("dialog")).toBeInTheDocument()
      expect(screen.getByRole("dialog").textContent).toMatch(/Cerid Trust Score/)
    })
  })

  describe("Color band logic", () => {
    it("score 90 → high band (green class)", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 90, band: "high" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.className).toMatch(/green/)
    })

    it("score 75 → medium band (amber class)", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 75, band: "medium" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.className).toMatch(/amber/)
    })

    it("score 60 → low band (red class)", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 60, band: "low" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.className).toMatch(/red/)
    })
  })

  describe("aria-label correct for each band", () => {
    it("high band aria-label", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 94, band: "high" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.getAttribute("aria-label")).toBe("System trust score: 94 of 100, high")
    })

    it("medium band aria-label", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 75, band: "medium" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.getAttribute("aria-label")).toBe("System trust score: 75 of 100, medium")
    })

    it("low band aria-label", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 60, band: "low" }))
      renderWithQuery(<TrustScoreChip />)
      const chip = await screen.findByTestId("trust-score-chip")
      expect(chip.getAttribute("aria-label")).toBe("System trust score: 60 of 100, low")
    })
  })

  describe("axe accessibility", () => {
    it("chip is axe-clean when loaded", async () => {
      mockedFetch.mockResolvedValue(makeScore())
      const { container } = renderWithQuery(<TrustScoreChip />)
      await screen.findByTestId("trust-score-chip")
      // Cast needed because jest-axe types don't export the exact Vitest override type
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  describe("renders nothing when API errors", () => {
    it("returns null on 404-style error — chip absent after error settles", async () => {
      mockedFetch.mockRejectedValue(new Error("Trust score fetch failed (404)"))
      // Use retryDelay: 0 so retries complete immediately instead of waiting ~1s
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: 0, retryDelay: 0, gcTime: 0, staleTime: 0 },
        },
      })
      renderWithQuery(<TrustScoreChip />, client)
      await waitFor(
        () => {
          expect(screen.queryByTestId("trust-score-chip")).not.toBeInTheDocument()
          expect(screen.queryByTestId("trust-score-skeleton")).not.toBeInTheDocument()
        },
        { timeout: 3000 },
      )
    })

    it("returns null on network error — chip absent after error settles", async () => {
      mockedFetch.mockRejectedValue(new TypeError("Failed to fetch"))
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: 0, retryDelay: 0, gcTime: 0, staleTime: 0 },
        },
      })
      renderWithQuery(<TrustScoreChip />, client)
      await waitFor(
        () => {
          expect(screen.queryByTestId("trust-score-chip")).not.toBeInTheDocument()
          expect(screen.queryByTestId("trust-score-skeleton")).not.toBeInTheDocument()
        },
        { timeout: 3000 },
      )
    })
  })

  describe("snapshots", () => {
    it("snapshot at rest (high band)", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 94, band: "high" }))
      const { container } = renderWithQuery(<TrustScoreChip />)
      await screen.findByTestId("trust-score-chip")
      expect(container.firstChild).toMatchSnapshot()
    })

    it("snapshot medium band", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 75, band: "medium" }))
      const { container } = renderWithQuery(<TrustScoreChip />)
      await screen.findByTestId("trust-score-chip")
      expect(container.firstChild).toMatchSnapshot()
    })

    it("snapshot low band", async () => {
      mockedFetch.mockResolvedValue(makeScore({ score: 60, band: "low" }))
      const { container } = renderWithQuery(<TrustScoreChip />)
      await screen.findByTestId("trust-score-chip")
      expect(container.firstChild).toMatchSnapshot()
    })
  })
})
