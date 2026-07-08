// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for the kNN neighbors panel (B5): populated + empty states, the
// re-pin callback, close, and axe-cleanliness in both states.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { SimilarNeighborsPanel } from "../similar-neighbors-panel"
import type { SimilarNeighbor } from "../similar-neighbors"

const neighbors: SimilarNeighbor[] = [
  { index: 2, id: "c", name: "Gamma", score: 0.9, normScore: 1 },
  { index: 3, id: "d", name: "Delta", score: 0.45, normScore: 0.5 },
]

describe("SimilarNeighborsPanel", () => {
  it("renders the pinned name and neighbor rows", () => {
    render(<SimilarNeighborsPanel pinnedName="Alpha" neighbors={neighbors} onPick={() => {}} onClose={() => {}} />)
    expect(screen.getByText("Alpha")).toBeTruthy()
    expect(screen.getByText("Gamma")).toBeTruthy()
    expect(screen.getByText("Delta")).toBeTruthy()
  })

  it("shows the empty state when there are no neighbors", () => {
    render(<SimilarNeighborsPanel pinnedName="Alpha" neighbors={[]} onPick={() => {}} onClose={() => {}} />)
    expect(screen.getByText(/no semantic neighbors yet/i)).toBeTruthy()
  })

  it("calls onPick with the neighbor index when a row is clicked", () => {
    const onPick = vi.fn()
    render(<SimilarNeighborsPanel pinnedName="Alpha" neighbors={neighbors} onPick={onPick} onClose={() => {}} />)
    fireEvent.click(screen.getByRole("button", { name: /focus gamma/i }))
    expect(onPick).toHaveBeenCalledWith(2)
  })

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn()
    render(<SimilarNeighborsPanel pinnedName="Alpha" neighbors={neighbors} onPick={() => {}} onClose={onClose} />)
    fireEvent.click(screen.getByRole("button", { name: /close similar/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it("is axe-clean with neighbors", async () => {
    const { container } = render(
      <SimilarNeighborsPanel pinnedName="Alpha" neighbors={neighbors} onPick={() => {}} onClose={() => {}} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean when empty", async () => {
    const { container } = render(
      <SimilarNeighborsPanel pinnedName="Alpha" neighbors={[]} onPick={() => {}} onClose={() => {}} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
