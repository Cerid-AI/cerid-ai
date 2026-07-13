// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
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

// ---------------------------------------------------------------------------
// Legibility: type labels, tooltips, keyboard reachability
// ---------------------------------------------------------------------------

describe("WorkflowCanvas — node legibility", () => {
  it("renders a type label on each node so the canvas is self-describing", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    const texts = Array.from(container.querySelectorAll("text")).map((el) => el.textContent)
    expect(texts).toContain("Agent")
    expect(texts).toContain("Condition")
  })

  it("exposes each node as a focusable button with an accessible name", () => {
    render(<WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />)
    const agentNode = screen.getByRole("button", { name: "query (Agent node)" })
    expect(agentNode).toBeInTheDocument()
    expect(agentNode).toHaveAttribute("tabindex", "0")
    expect(screen.getByRole("button", { name: "check (Condition node)" })).toBeInTheDocument()
  })

  it("wraps every node in a tooltip trigger", () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    expect(container.querySelectorAll('[data-slot="tooltip-trigger"]').length).toBe(mockNodes.length)
  })

  it("shows the node's purpose in a tooltip on focus", async () => {
    render(<WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />)
    fireEvent.focus(screen.getByRole("button", { name: "query (Agent node)" }))
    const purpose = await screen.findAllByText(/retrieves the most relevant knowledge-base entries/i)
    expect(purpose.length).toBeGreaterThan(0)
  })

  it("selects a node via keyboard (Enter)", () => {
    const onNodeClick = vi.fn()
    render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} onNodeClick={onNodeClick} />,
    )
    fireEvent.keyDown(screen.getByRole("button", { name: "query (Agent node)" }), { key: "Enter" })
    expect(onNodeClick).toHaveBeenCalledWith("query_1")
  })

  it("selects a node via keyboard (Space)", () => {
    const onNodeClick = vi.fn()
    render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} onNodeClick={onNodeClick} />,
    )
    fireEvent.keyDown(screen.getByRole("button", { name: "check (Condition node)" }), { key: " " })
    expect(onNodeClick).toHaveBeenCalledWith("condition_2")
  })

  it("is axe-clean with nodes rendered", async () => {
    const { container } = render(
      <WorkflowCanvas nodes={mockNodes} edges={mockEdges} selectedNodeId={null} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
