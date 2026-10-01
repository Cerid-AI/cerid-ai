// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RelevanceBar } from "@/components/ui/relevance-bar"
import type { KBQueryResult, MemoryRecallResult } from "@/lib/types"

vi.mock("@/hooks/use-settings", () => ({
  useSettings: () => ({ autoInject: false, toggleAutoInject: vi.fn() }),
}))

const recalled: MemoryRecallResult[] = [
  { memory_id: "m1", content: "first", summary: "first memory", memory_type: "empirical", relevance: 0.8, age_days: 2 } as MemoryRecallResult,
  { memory_id: "m2", content: "second", summary: "second memory", memory_type: "decision", relevance: 0.4, age_days: 9 } as MemoryRecallResult,
]

vi.mock("@/lib/api", () => ({
  uploadFile: vi.fn(),
  fetchKBStats: vi.fn().mockResolvedValue(null),
  recallMemories: vi.fn().mockImplementation(() => Promise.resolve(recalled)),
}))

import { KBContextPanel } from "@/components/kb/kb-context-panel"

function bar(name: string) {
  return screen.getByRole("progressbar", { name })
}

describe("RelevanceBar", () => {
  it("fills the best match and scales the others against it", () => {
    const scores = [0.84, 0.52, 0.21]
    render(
      <>
        {scores.map((s) => <RelevanceBar key={s} relevance={s} among={scores} />)}
      </>,
    )
    expect(bar("Relevance: 1 of 3 results, 1.00 relative to the best match")).toHaveAttribute("aria-valuenow", "100")
    expect(bar("Relevance: 2 of 3 results, 0.62 relative to the best match")).toHaveAttribute("aria-valuenow", "62")
    expect(bar("Relevance: 3 of 3 results, 0.25 relative to the best match")).toHaveAttribute("aria-valuenow", "25")
  })

  it("stays full for a best match the backend boosted past 1.0", () => {
    render(<RelevanceBar relevance={1.3} among={[1.3, 0.65]} />)
    expect(bar("Relevance: 1 of 2 results, 1.00 relative to the best match")).toHaveAttribute("aria-valuenow", "100")
  })

  it("gives tied scores the same rank", () => {
    const scores = [0.5, 0.5]
    render(<>{scores.map((s, i) => <RelevanceBar key={i} relevance={s} among={scores} />)}</>)
    expect(screen.getAllByRole("progressbar", { name: "Relevance: 1 of 2 results, 1.00 relative to the best match" })).toHaveLength(2)
  })

  it("measures a source that stands alone against 1.0 and says so", () => {
    render(<RelevanceBar relevance={0.87} />)
    expect(bar("Relevance: 0.87 out of 1.00, single source")).toHaveAttribute("aria-valuenow", "87")
  })

  it("prints no percentage and is axe-clean", async () => {
    const { container } = render(<RelevanceBar relevance={0.5} among={[1, 0.5]} />)
    expect(container.textContent).not.toMatch(/%/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("KBContextPanel memories", () => {
  it("shows each recalled memory's relevance as a relative bar, not a percentage", async () => {
    const result: KBQueryResult = {
      content: "chunk", relevance: 0.6, artifact_id: "a1", filename: "notes.md",
      domain: "general", chunk_index: 0, collection: "kb_general", ingested_at: "2026-01-15T10:00:00Z",
    }
    render(
      <KBContextPanel
        results={[result]}
        confidence={0.6}
        totalResults={1}
        executionTime={1}
        isLoading={false}
        error={null}
        isError={false}
        refetch={vi.fn()}
        hasQueried
        activeDomains={new Set()}
        toggleDomain={vi.fn()}
        activeTags={[]}
        toggleTag={vi.fn()}
        manualQuery=""
        setManualQuery={vi.fn()}
        executeManualSearch={vi.fn()}
        clearManualSearch={vi.fn()}
        selectedArtifactId={null}
        setSelectedArtifactId={vi.fn()}
        injectedContext={[]}
        injectResult={vi.fn()}
        removeInjected={vi.fn()}
        clearInjected={vi.fn()}
        onClose={vi.fn()}
      />,
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            {children}
          </QueryClientProvider>
        ),
      },
    )
    await waitFor(() => expect(screen.getByText("second memory")).toBeInTheDocument())
    expect(bar("Relevance: 1 of 2 results, 1.00 relative to the best match")).toBeInTheDocument()
    expect(bar("Relevance: 2 of 2 results, 0.50 relative to the best match")).toBeInTheDocument()
    expect(bar("Relevance: 1 of 1 result, 1.00 relative to the best match")).toBeInTheDocument()
    expect(screen.queryByText(/^\d+%$/)).toBeNull()
  })
})
