// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// AtlasA11yTree exposes per-node viewport coordinates so assistive tooling
// (and the E2E hover probe) can target a real node instead of guessing —
// see docs/superpowers/plans/2026-09-09-readiness-c-backend-tests-ops.md C5.

import { describe, it, expect, vi } from "vitest"
import { render } from "@testing-library/react"
import Graph from "graphology"
import { AtlasA11yTree } from "@/components/subjects/atlas/atlas-a11y-tree"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

function attrs(over: Partial<AtlasNodeAttributes> = {}): AtlasNodeAttributes {
  return {
    id: "n",
    name: "Node",
    type: "bordered",
    entityType: "Person",
    community: null,
    mention_count: 3,
    trust_state: "verified",
    recency_score: 0.5,
    focused: false,
    x: 0,
    y: 0,
    size: 8,
    label: "Node",
    color: "#A0A0FF", // drift-allowed: test stub only
    haloColor: "#A0A0FF", // drift-allowed: test stub only
    pulseIntensity: 0.5,
    ...over,
  }
}

function mkGraph(): Graph<AtlasNodeAttributes, AtlasEdgeAttributes> {
  const g = new Graph<AtlasNodeAttributes, AtlasEdgeAttributes>({ multi: false, allowSelfLoops: false })
  g.addNode("focal", attrs({ name: "Focal Node", focused: true }))
  g.addNode("other", attrs({ name: "Other Node" }))
  return g
}

describe("AtlasA11yTree", () => {
  it("stamps each list item with data-node-id", () => {
    const { container } = render(
      <AtlasA11yTree
        graph={mkGraph()}
        selectedNodeId={null}
        onSelect={vi.fn()}
        focalEntity="focal"
        positions={new Map()}
      />,
    )
    const focal = container.querySelector('li[data-node-id="focal"]')
    const other = container.querySelector('li[data-node-id="other"]')
    expect(focal).not.toBeNull()
    expect(other).not.toBeNull()
  })

  it("stamps data-x/data-y in viewport pixels once a position is known", () => {
    const positions = new Map([["focal", { x: 123.4, y: 56.9 }]])
    const { container } = render(
      <AtlasA11yTree
        graph={mkGraph()}
        selectedNodeId={null}
        onSelect={vi.fn()}
        focalEntity="focal"
        positions={positions}
      />,
    )
    const focal = container.querySelector('li[data-node-id="focal"]')
    expect(focal?.getAttribute("data-x")).toBe("123")
    expect(focal?.getAttribute("data-y")).toBe("57")
  })

  it("omits data-x/data-y before layout has settled", () => {
    const { container } = render(
      <AtlasA11yTree
        graph={mkGraph()}
        selectedNodeId={null}
        onSelect={vi.fn()}
        focalEntity="focal"
        positions={new Map()}
      />,
    )
    const other = container.querySelector('li[data-node-id="other"]')
    expect(other?.hasAttribute("data-x")).toBe(false)
    expect(other?.hasAttribute("data-y")).toBe(false)
  })

  it("keeps the focal node's aria-label carrying 'focal entity'", () => {
    const { container } = render(
      <AtlasA11yTree
        graph={mkGraph()}
        selectedNodeId={null}
        onSelect={vi.fn()}
        focalEntity="focal"
        positions={new Map()}
      />,
    )
    const focal = container.querySelector('li[data-node-id="focal"]')
    expect(focal?.getAttribute("aria-label")).toContain("focal entity")
  })
})
