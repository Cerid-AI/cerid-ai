// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { SourceAttribution } from "@/components/chat/source-attribution"
import type { SourceRef } from "@/lib/types"

const mockSources: SourceRef[] = [
  {
    artifact_id: "art-1",
    filename: "auth-middleware.py",
    domain: "coding",
    sub_category: "python",
    relevance: 0.92,
    chunk_index: 0,
    tags: ["fastapi", "auth"],
  },
  {
    artifact_id: "art-2",
    filename: "budget-2025.xlsx",
    domain: "finance",
    relevance: 0.78,
    chunk_index: 1,
  },
]

describe("SourceAttribution", () => {
  it("renders nothing when sources array is empty", () => {
    const { container } = render(<SourceAttribution sources={[]} />)
    expect(container.innerHTML).toBe("")
  })

  it("shows source count in collapsed state", () => {
    render(<SourceAttribution sources={mockSources} />)
    expect(screen.getByText("2 sources")).toBeInTheDocument()
  })

  it("uses singular 'source' for single item", () => {
    render(<SourceAttribution sources={[mockSources[0]]} />)
    expect(screen.getByText("1 source")).toBeInTheDocument()
  })

  it("expands to show source cards on click", async () => {
    const user = userEvent.setup()
    render(<SourceAttribution sources={mockSources} />)

    // Initially filenames should not be visible
    expect(screen.queryByText("auth-middleware.py")).not.toBeInTheDocument()

    // Click the trigger
    await user.click(screen.getByText("2 sources"))

    // Now filenames should be visible
    expect(screen.getByText("auth-middleware.py")).toBeInTheDocument()
    expect(screen.getByText("budget-2025.xlsx")).toBeInTheDocument()
  })

  it("shows relevance percentages", async () => {
    const user = userEvent.setup()
    render(<SourceAttribution sources={mockSources} />)
    await user.click(screen.getByText("2 sources"))

    expect(screen.getByText("92%")).toBeInTheDocument()
    expect(screen.getByText("78%")).toBeInTheDocument()
  })

  it("shows sub-category badge when present and not 'general'", async () => {
    const user = userEvent.setup()
    render(<SourceAttribution sources={mockSources} />)
    await user.click(screen.getByText("2 sources"))

    expect(screen.getByText("python")).toBeInTheDocument()
  })

  it("does not show sub-category badge for 'general'", async () => {
    const user = userEvent.setup()
    const sources: SourceRef[] = [
      { artifact_id: "a1", filename: "file.txt", domain: "general", sub_category: "general", relevance: 0.5, chunk_index: 0 },
    ]
    render(<SourceAttribution sources={sources} />)
    await user.click(screen.getByText("1 source"))

    // "general" should appear as domain badge but NOT as sub_category badge
    const generalBadges = screen.getAllByText("general")
    expect(generalBadges).toHaveLength(1) // only the domain badge
  })

  it("collapses when clicked again", async () => {
    const user = userEvent.setup()
    render(<SourceAttribution sources={mockSources} />)

    await user.click(screen.getByText("2 sources"))
    expect(screen.getByText("auth-middleware.py")).toBeInTheDocument()

    await user.click(screen.getByText("2 sources"))
    // After collapsing, the content should be hidden
    expect(screen.queryByText("auth-middleware.py")).not.toBeInTheDocument()
  })

  it("shows domain badges for each source", async () => {
    const user = userEvent.setup()
    render(<SourceAttribution sources={mockSources} />)
    await user.click(screen.getByText("2 sources"))

    expect(screen.getByText("coding")).toBeInTheDocument()
    expect(screen.getByText("finance")).toBeInTheDocument()
  })

  it("deduplicates multiple chunks from the same artifact", async () => {
    const user = userEvent.setup()
    const sources: SourceRef[] = [
      { artifact_id: "art-1", filename: "report.pdf", domain: "research", relevance: 0.85, chunk_index: 0 },
      { artifact_id: "art-1", filename: "report.pdf", domain: "research", relevance: 0.72, chunk_index: 1 },
      { artifact_id: "art-1", filename: "report.pdf", domain: "research", relevance: 0.60, chunk_index: 2 },
      { artifact_id: "art-2", filename: "notes.md", domain: "general", relevance: 0.55, chunk_index: 0 },
    ]
    render(<SourceAttribution sources={sources} />)

    // Should show 2 deduplicated sources, not 4
    expect(screen.getByText("2 sources")).toBeInTheDocument()

    await user.click(screen.getByText("2 sources"))
    // Only one "report.pdf" card
    expect(screen.getAllByText("report.pdf")).toHaveLength(1)
    // Highest relevance kept
    expect(screen.getByText("85%")).toBeInTheDocument()
  })
})
