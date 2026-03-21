// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render } from "@testing-library/react"
import WorkflowCanvas from "@/components/workflows/workflow-canvas"

beforeEach(() => {
  vi.restoreAllMocks()
})

const mockNodes = [
  { id: "query_1", type: "agent" as const, name: "query", config: {}, position: { x: 50, y: 200 } },
  { id: "condition_2", type: "condition" as const, name: "check", config: { expression: "confidence > 0.5" }, position: { x: 270, y: 200 } },
]

const mockEdges = [
  { source_id: "query_1", target_id: "condition_2", label: "pass", condition: null },
]

describe("WorkflowCanvas", () => {
  it("renders an SVG element", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    expect(container.querySelector("svg")).toBeTruthy()
  })

  it("renders node name text elements", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    const textEls = container.querySelectorAll("text")
    const texts = Array.from(textEls).map((el) => el.textContent)
    expect(texts).toContain("query")
    expect(texts).toContain("check")
  })

  it("renders edge path elements", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    const paths = container.querySelectorAll("path")
    expect(paths.length).toBeGreaterThan(0)
  })

  it("renders edge label text", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    const textEls = container.querySelectorAll("text")
    const texts = Array.from(textEls).map((el) => el.textContent)
    expect(texts).toContain("pass")
  })
})
