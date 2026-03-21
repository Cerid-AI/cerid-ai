// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import WorkflowEditor from "@/components/workflows/workflow-editor"

vi.mock("@/lib/api", () => ({
  createWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
  runWorkflow: vi.fn(),
  fetchWorkflowTemplates: vi.fn().mockResolvedValue([
    { id: "tpl-1", name: "Simple Pipeline", description: "A basic agent pipeline", nodes: [], edges: [] },
  ]),
}))

const noop = () => {}

const mockWorkflow = {
  id: "wf-1",
  name: "Test Workflow",
  description: "A test workflow",
  nodes: [
    { id: "query_abc", type: "agent" as const, name: "query", config: {}, position: { x: 50, y: 200 } },
    { id: "curator_def", type: "agent" as const, name: "curator", config: {}, position: { x: 270, y: 200 } },
  ],
  edges: [{ source_id: "query_abc", target_id: "curator_def", label: null, condition: null }],
  enabled: true,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("WorkflowEditor", () => {
  it("renders Save button", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.getByText("Save")).toBeInTheDocument()
  })

  it("renders Templates dropdown button", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.getByText("Templates")).toBeInTheDocument()
  })

  it("renders workflow name when editing existing workflow", () => {
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    const nameInput = screen.getByDisplayValue("Test Workflow")
    expect(nameInput).toBeInTheDocument()
  })

  it("renders Add Node and Delete buttons", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.getByText("Add Node")).toBeInTheDocument()
    expect(screen.getByText("Delete")).toBeInTheDocument()
  })
})
