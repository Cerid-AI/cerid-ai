// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

// Mock the Atlas component: the real one imports sigma.js, which evaluates
// WebGL2RenderingContext at module load and is undefined under jsdom. The
// mock lets us assert the panel mounts without booting a WebGL renderer.
vi.mock("@/components/subjects/atlas/Atlas", () => ({
  Atlas: ({ entity }: { entity: string }) => (
    <div data-testid="atlas-mock">atlas:{entity}</div>
  ),
}))

vi.mock("@/contexts/navigation-context", () => ({
  useNavigation: () => ({ goTo: vi.fn() }),
}))

const fetchNeighborhood = vi.fn()
vi.mock("@/lib/api/graph", () => ({
  fetchNeighborhood: (...args: unknown[]) => fetchNeighborhood(...args),
}))

import { MiniGraph } from "./mini-graph"

describe("MiniGraph", () => {
  beforeEach(() => {
    fetchNeighborhood.mockReset()
    fetchNeighborhood.mockResolvedValue({ nodes: [], edges: [] })
  })

  it("renders the graph panel collapsed by default (no panel without a click)", () => {
    render(<MiniGraph entitySlug="other:python" entityName="Python" />)
    expect(screen.queryByTestId("wiki-minigraph-panel")).not.toBeInTheDocument()
  })

  it("renders the graph panel WITHOUT a click when defaultExpanded is set", async () => {
    render(
      <MiniGraph
        entitySlug="other:python"
        entityName="Python"
        defaultExpanded
      />,
    )
    expect(screen.getByTestId("wiki-minigraph-panel")).toBeInTheDocument()
    // The lazy Atlas renderer mounts (suspense resolves to the mock).
    await waitFor(() =>
      expect(screen.getByTestId("atlas-mock")).toBeInTheDocument(),
    )
  })

  it("fetches the neighborhood when expanded by default", async () => {
    render(
      <MiniGraph
        entitySlug="other:python"
        entityName="Python"
        defaultExpanded
      />,
    )
    await waitFor(() =>
      expect(fetchNeighborhood).toHaveBeenCalledWith("other:python", 1),
    )
  })
})
