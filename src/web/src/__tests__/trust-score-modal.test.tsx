// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TrustScoreModal } from "@/components/trust-score"
import type { TrustScore, TrustComponent } from "@/lib/types/trust-score"

const makeComponent = (
  id: string,
  label: string,
  overrides: Partial<TrustComponent> = {},
): TrustComponent => ({
  id,
  label,
  value: 0.92,
  target: 0.9,
  normalized: 1.0,
  status: "ok",
  source: "nightly run",
  last_updated_at: "2026-05-10T02:00:00Z",
  note: null,
  ...overrides,
})

const makeScore = (overrides: Partial<TrustScore> = {}): TrustScore => ({
  score: 87,
  band: "high",
  updated_at: "2026-05-10T06:00:00Z",
  components: [
    makeComponent("faithfulness", "Faithfulness"),
    makeComponent("retrieval_ndcg10", "Retrieval (NDCG@10)", { value: 0.87, target: 0.85 }),
    makeComponent("memory_recall", "Memory recall (LongMemEval)", {
      value: null,
      target: 0.8,
      normalized: null,
      status: "not_available",
      last_updated_at: null,
      note: "longmemeval.json not yet generated",
    }),
    makeComponent("verification_coverage", "Verification coverage", {
      value: 0.97,
      target: 0.95,
      note: "97/100",
    }),
    makeComponent("preservation_health", "Preservation health", {
      value: null,
      target: 1.0,
      normalized: null,
      status: "not_available",
      last_updated_at: null,
      note: "preservation.json not yet written by CI",
    }),
  ],
  ...overrides,
})

describe("TrustScoreModal", () => {
  describe("modal opens with correct tabs for all components", () => {
    it("renders a tab for each component", () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      expect(screen.getByRole("tab", { name: "Faithfulness" })).toBeInTheDocument()
      expect(screen.getByRole("tab", { name: /Retrieval/i })).toBeInTheDocument()
      expect(screen.getByRole("tab", { name: /Memory recall/i })).toBeInTheDocument()
      expect(screen.getByRole("tab", { name: /Verification/i })).toBeInTheDocument()
      expect(screen.getByRole("tab", { name: /Preservation/i })).toBeInTheDocument()
    })
  })

  describe("each tab renders value, target, status, source link", () => {
    it("faithfulness tab shows value and target", async () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      // First tab is active by default — faithfulness
      // 0.92 → 92%
      expect(screen.getAllByText(/92%/)[0]).toBeInTheDocument()
      // target 0.9 → 90%
      expect(screen.getAllByText(/90%/)[0]).toBeInTheDocument()
    })

    it("faithfulness tab shows OK status pill", () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      expect(screen.getByText("OK")).toBeInTheDocument()
    })

    it("faithfulness tab shows source documentation link", () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      const link = screen.getByRole("link", { name: /source documentation/i })
      expect(link).toBeInTheDocument()
      expect(link.getAttribute("href")).toMatch(/EVAL_BASELINES/)
    })
  })

  describe("sparkline section (V-P2.2)", () => {
    // V-P2.2: sparkline section is hidden entirely until per-component history
    // ships from the backend. The dashed "Insufficient history" placeholder
    // was permanent visual noise. Re-add a test when history wires through.
    it("does not render the trend section while history is unwired", () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      expect(screen.queryByText("Insufficient history")).not.toBeInTheDocument()
      expect(screen.queryByText(/Trend \(last 7 days\)/i)).not.toBeInTheDocument()
    })
  })

  describe("keyboard navigation through tabs", () => {
    it("can navigate to retrieval tab by clicking", async () => {
      const user = userEvent.setup()
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      const retrievalTab = screen.getByRole("tab", { name: /Retrieval/i })
      await user.click(retrievalTab)
      expect(retrievalTab).toHaveAttribute("data-state", "active")
    })

    it("'How is this calculated?' collapses and expands", async () => {
      const user = userEvent.setup()
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      const toggle = screen.getByRole("button", { name: /How is this calculated/i })
      expect(toggle).toHaveAttribute("aria-expanded", "false")
      await user.click(toggle)
      expect(toggle).toHaveAttribute("aria-expanded", "true")
      // Calculation text should now be visible
      expect(screen.getByText(/RAGAS evaluation/i)).toBeInTheDocument()
    })
  })

  describe("not_available components", () => {
    it("shows 'Not available' status for missing components", async () => {
      const user = userEvent.setup()
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore()} />,
      )
      await user.click(screen.getByRole("tab", { name: /Memory recall/i }))
      const dialog = screen.getByRole("dialog")
      expect(within(dialog).getByText("Not available")).toBeInTheDocument()
    })
  })

  describe("empty state", () => {
    it("shows empty state when no components provided", () => {
      render(
        <TrustScoreModal
          open={true}
          onOpenChange={vi.fn()}
          data={makeScore({ components: [] })}
        />,
      )
      expect(screen.getByText(/No component data available/i)).toBeInTheDocument()
    })
  })

  describe("title shows score and band", () => {
    it("renders score and band in dialog header", () => {
      render(
        <TrustScoreModal open={true} onOpenChange={vi.fn()} data={makeScore({ score: 87, band: "high" })} />,
      )
      const dialog = screen.getByRole("dialog")
      expect(dialog.textContent).toMatch(/87/)
      expect(dialog.textContent).toMatch(/high/i)
    })
  })
})
