// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for the structural-gaps panel (C2): 4 states (loading/error/empty/
// success), the explore + hover + close callbacks, and axe-cleanliness.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { StructuralGapsPanel } from "../structural-gaps-panel"
import type { StructuralGap } from "@/lib/api/graph-structural-gaps"

const gaps: StructuralGap[] = [
  {
    community_a: { id: "c1", label: "Rust tooling", count: 40 },
    community_b: { id: "c2", label: "Build systems", count: 31 },
    semantic_similarity: 0.82,
    link_strength: 0.1,
    gap_score: 0.74,
    bridging_candidates: [
      { id: "e1", name: "cargo" },
      { id: "e2", name: "bazel" },
    ],
  },
  {
    community_a: { id: "c3", label: "Finance", count: 22 },
    community_b: { id: "c4", label: "Taxes", count: 18 },
    semantic_similarity: 0.7,
    link_strength: 0.3,
    gap_score: 0.49,
    bridging_candidates: [{ id: "e3", name: "IRS" }],
  },
]

function baseProps() {
  return {
    gaps,
    isLoading: false,
    isError: false,
    onClose: () => {},
    onExplore: () => {},
    onHoverGap: () => {},
  }
}

describe("StructuralGapsPanel", () => {
  it("renders a loading state", () => {
    render(<StructuralGapsPanel {...baseProps()} gaps={[]} isLoading />)
    expect(screen.getByRole("status", { name: /loading/i })).toBeTruthy()
  })

  it("renders an error state", () => {
    render(<StructuralGapsPanel {...baseProps()} gaps={[]} isError errorMessage="boom" />)
    expect(screen.getByText(/boom/i)).toBeTruthy()
  })

  it("renders an empty state when there are no gaps", () => {
    render(<StructuralGapsPanel {...baseProps()} gaps={[]} />)
    expect(screen.getByText(/no structural gaps/i)).toBeTruthy()
  })

  it("renders each gap's community labels and bridging candidates", () => {
    render(<StructuralGapsPanel {...baseProps()} />)
    expect(screen.getByText("Rust tooling")).toBeTruthy()
    expect(screen.getByText("Build systems")).toBeTruthy()
    expect(screen.getByText("cargo")).toBeTruthy()
    expect(screen.getByText("bazel")).toBeTruthy()
  })

  it("calls onExplore with the gap when Explore in chat is clicked", () => {
    const onExplore = vi.fn()
    render(<StructuralGapsPanel {...baseProps()} onExplore={onExplore} />)
    const buttons = screen.getAllByRole("button", { name: /explore in chat/i })
    fireEvent.click(buttons[0])
    expect(onExplore).toHaveBeenCalledWith(gaps[0])
  })

  it("calls onHoverGap on mouse enter/leave of a gap row", () => {
    const onHoverGap = vi.fn()
    render(<StructuralGapsPanel {...baseProps()} onHoverGap={onHoverGap} />)
    const row = screen.getByRole("group", { name: /Rust tooling.*Build systems/i })
    fireEvent.mouseEnter(row)
    expect(onHoverGap).toHaveBeenCalledWith(gaps[0])
    fireEvent.mouseLeave(row)
    expect(onHoverGap).toHaveBeenCalledWith(null)
  })

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn()
    render(<StructuralGapsPanel {...baseProps()} onClose={onClose} />)
    fireEvent.click(screen.getByRole("button", { name: /close structural gaps/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it("is axe-clean with gaps", async () => {
    const { container } = render(<StructuralGapsPanel {...baseProps()} />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean when empty", async () => {
    const { container } = render(<StructuralGapsPanel {...baseProps()} gaps={[]} />)
    expect(await axe(container)).toHaveNoViolations()
  })
})
