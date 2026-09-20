// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// F366 — CosmosLive instantiated @cosmos.gl/graph unconditionally. On a
// browser that cannot hand out a WebGL2 context (blocklisted GPU, VM or
// remote desktop, hardware acceleration off, or the per-document context
// budget already exhausted -- CartographerMap documents "Too many active
// WebGL contexts" as observed) the GPU pipeline dereferenced a null context
// and threw `Cannot read properties of null (reading 'blendFunc')`. The throw
// escaped to App.tsx's pane-level PaneErrorBoundary, so the whole Subjects
// pane went down -- Atlas, Timeline, Wiki and Communities with it -- behind a
// raw TypeError and a Retry button that re-throws.
//
// The graph itself needs a GPU, so the constructor is stubbed; what is under
// test is whether CosmosLive asks for one before handing over the canvas.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

const graphCtor = vi.fn()

vi.mock("@cosmos.gl/graph", () => ({
  Graph: class {
    ready = Promise.resolve()
    constructor(...args: unknown[]) {
      graphCtor(...args)
    }
    render() {}
    fitView() {}
    start() {}
    pause() {}
    destroy() {}
    setConfig() {}
    setPointPositions() {}
    setPointColors() {}
    setLinks() {}
  },
}))

const { CosmosLive } = await import("@/components/subjects/constellation/cosmos-live")

const props = {
  entities: [
    {
      id: "a",
      name: "A",
      x: 0,
      y: 0,
      z: 0,
      type: "person",
      community: null,
      mention_count: 1,
      trust_state: "unverified",
      projection: "umap" as const,
    },
    {
      id: "b",
      name: "B",
      x: 1,
      y: 1,
      z: 1,
      type: "person",
      community: null,
      mention_count: 1,
      trust_state: "unverified",
      projection: "umap" as const,
    },
  ],
  links: [[0, 1, 1, "related"]] as [number, number, number, string][],
  playing: true,
  repulsion: 0.5,
  bigBangNonce: 0,
  reducedMotion: false,
  background: "#000000", // drift-allowed: test fixture for the theme-routed background prop, not UI colour
  onNodeClick: vi.fn(),
}

const realGetContext = HTMLCanvasElement.prototype.getContext

beforeEach(() => {
  graphCtor.mockReset()
})

afterEach(() => {
  cleanup()
  HTMLCanvasElement.prototype.getContext = realGetContext
})

describe("CosmosLive — no WebGL2 context", () => {
  // jsdom implements no WebGL at all, which is the environment the guard is for.
  it("does not hand the canvas to the GPU graph", () => {
    render(<CosmosLive {...props} />)
    expect(graphCtor).not.toHaveBeenCalled()
  })

  it("degrades to an in-place notice instead of taking the pane down", () => {
    render(<CosmosLive {...props} />)
    expect(screen.getByText(/needs WebGL2/i)).toBeTruthy()
  })

  it("keeps the notice readable by assistive tech", () => {
    render(<CosmosLive {...props} />)
    // The live canvas is aria-hidden (a decorative GPU surface); the fallback
    // that replaces it must not inherit that.
    expect(screen.getByText(/needs WebGL2/i).closest("[aria-hidden='true']")).toBeNull()
  })

  it("points the user at the Subjects modes that still work", () => {
    render(<CosmosLive {...props} />)
    expect(screen.getByText(/Atlas/i)).toBeTruthy()
  })
})

describe("CosmosLive — WebGL2 available", () => {
  beforeEach(() => {
    HTMLCanvasElement.prototype.getContext = vi.fn((kind: string) =>
      kind === "webgl2"
        ? ({ getExtension: () => ({ loseContext: () => {} }) } as unknown as RenderingContext)
        : null,
    ) as unknown as typeof HTMLCanvasElement.prototype.getContext
  })

  it("builds the graph and shows no fallback", () => {
    render(<CosmosLive {...props} />)
    expect(graphCtor).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/needs WebGL2/i)).toBeNull()
  })
})
