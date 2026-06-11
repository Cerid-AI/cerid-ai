// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase M Day 5 — frontend tests for the wiki mini-visualization
// components (MentionSparkline, ProvenanceSankey, ContradictionLink).

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MentionSparkline } from "@/components/wiki/mention-sparkline"
import { ProvenanceSankey } from "@/components/wiki/provenance-sankey"
import { ContradictionLink } from "@/components/wiki/contradiction-link"
import type { SourceCitation } from "@/lib/types/wiki"

// Recharts uses ResizeObserver — jsdom doesn't have it. Stub before render.
class _RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof _RO }).ResizeObserver = _RO

const mockFetchTimeline = vi.fn()
const mockFetchNeighborhood = vi.fn()

vi.mock("@/lib/api/graph", () => ({
  fetchTimeline: (...a: unknown[]) => mockFetchTimeline(...a),
  fetchNeighborhood: (...a: unknown[]) => mockFetchNeighborhood(...a),
}))

beforeEach(() => {
  mockFetchTimeline.mockReset()
  mockFetchNeighborhood.mockReset()
})

function makeSource(overrides: Partial<SourceCitation> = {}): SourceCitation {
  return {
    artifact_id: "art-001",
    title: "Test Document",
    filename: "test-doc.md",
    domain: "notes",
    source_type: "file",
    confidence: 0.85,
    updated_at: null,
    ...overrides,
  }
}


describe("MentionSparkline", () => {
  it("does not fetch until expanded", async () => {
    render(<MentionSparkline entitySlug="tesla" entityName="Tesla" />)
    expect(mockFetchTimeline).not.toHaveBeenCalled()
  })

  it("expands and fetches on toggle", async () => {
    mockFetchTimeline.mockResolvedValue({
      entity: "tesla",
      from_date: "2026-02-21",
      to_date: "2026-05-22",
      granularity: "day",
      buckets: [
        { date: "2026-05-20", mention_count: 3, entities_introduced: 0 },
        { date: "2026-05-21", mention_count: 7, entities_introduced: 1 },
      ],
      total_mentions: 10,
      total_entities_introduced: 1,
      cached: false,
    })
    const user = userEvent.setup()
    render(<MentionSparkline entitySlug="tesla" entityName="Tesla" />)
    await user.click(screen.getByRole("button", { name: /mention trend/i }))
    await screen.findByTestId("mention-sparkline-open-timeline")
    expect(mockFetchTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ entity: "tesla", period: "90d" }),
    )
  })

  it("calls onOpenTimeline when the deep link is clicked", async () => {
    mockFetchTimeline.mockResolvedValue({
      entity: "tesla",
      from_date: "2026-02-21",
      to_date: "2026-05-22",
      granularity: "day",
      buckets: [
        { date: "2026-05-21", mention_count: 7, entities_introduced: 1 },
      ],
      total_mentions: 7,
      total_entities_introduced: 1,
      cached: false,
    })
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(
      <MentionSparkline
        entitySlug="tesla"
        entityName="Tesla"
        onOpenTimeline={onOpen}
      />,
    )
    await user.click(screen.getByRole("button", { name: /mention trend/i }))
    const link = await screen.findByTestId("mention-sparkline-open-timeline")
    await user.click(link)
    expect(onOpen).toHaveBeenCalledWith("tesla")
  })
})


describe("ProvenanceSankey", () => {
  it("does not render body until expanded", async () => {
    render(
      <ProvenanceSankey
        entitySlug="tesla"
        entityName="Tesla"
        sourceArtifacts={[]}
      />,
    )
    expect(screen.queryByText(/No sources recorded/i)).toBeNull()
  })

  it("renders empty-state when no source artifacts", async () => {
    const user = userEvent.setup()
    render(
      <ProvenanceSankey
        entitySlug="tesla"
        entityName="Tesla"
        sourceArtifacts={[]}
      />,
    )
    await user.click(screen.getByRole("button", { name: /provenance flow/i }))
    await screen.findByText(/No sources recorded/i)
  })

  it("does not call fetchNeighborhood (data comes from sourceArtifacts prop)", async () => {
    const user = userEvent.setup()
    render(
      <ProvenanceSankey
        entitySlug="tesla"
        entityName="Tesla"
        sourceArtifacts={[makeSource()]}
      />,
    )
    await user.click(screen.getByRole("button", { name: /provenance flow/i }))
    expect(mockFetchNeighborhood).not.toHaveBeenCalled()
  })

  it("opens Atlas deep-link on click", async () => {
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(
      <ProvenanceSankey
        entitySlug="tesla"
        entityName="Tesla"
        sourceArtifacts={[makeSource(), makeSource({ artifact_id: "art-002", source_type: "vault" })]}
        onOpenAtlas={onOpen}
      />,
    )
    await user.click(screen.getByRole("button", { name: /provenance flow/i }))
    const link = await screen.findByTestId("provenance-sankey-open-atlas")
    await user.click(link)
    expect(onOpen).toHaveBeenCalledWith("tesla")
  })

  it("shows source count in summary line", async () => {
    const user = userEvent.setup()
    render(
      <ProvenanceSankey
        entitySlug="tesla"
        entityName="Tesla"
        sourceArtifacts={[makeSource(), makeSource({ artifact_id: "art-002" })]}
      />,
    )
    await user.click(screen.getByRole("button", { name: /provenance flow/i }))
    await screen.findByText(/2 sources recorded/i)
  })
})


describe("ContradictionLink", () => {
  it("renders nothing when no contradictions", () => {
    const { container } = render(
      <ContradictionLink entitySlug="tesla" contradictionCount={0} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("renders pluralized label and fires callback", async () => {
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(
      <ContradictionLink
        entitySlug="tesla"
        contradictionCount={3}
        onOpenAtlas={onOpen}
      />,
    )
    const btn = screen.getByTestId("contradiction-link")
    expect(btn).toHaveTextContent(/3 contradictions/)
    await user.click(btn)
    expect(onOpen).toHaveBeenCalledWith("tesla")
  })

  it("uses singular form when count is 1", () => {
    render(<ContradictionLink entitySlug="tesla" contradictionCount={1} />)
    expect(screen.getByTestId("contradiction-link")).toHaveTextContent(/1 contradiction —/)
  })
})
