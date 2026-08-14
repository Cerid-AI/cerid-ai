// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import WorkflowEditor from "@/components/workflows/workflow-editor"

vi.mock("@/lib/api", async () => {
  const { FALLBACK_NODE_CATALOG } = await import("@/components/workflows/node-catalog")
  return {
    createWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    runWorkflow: vi.fn(),
    fetchWorkflowTemplates: vi.fn().mockResolvedValue([
      { id: "tpl-1", name: "Simple Pipeline", description: "A basic agent pipeline", nodes: [], edges: [] },
    ]),
    fetchWorkflowNodeTypes: vi.fn().mockResolvedValue(FALLBACK_NODE_CATALOG),
  }
})

const noop = () => {}

const mockWorkflow = {
  id: "wf-1",
  name: "Test Workflow",
  description: "A test workflow",
  nodes: [
    { id: "query_abc", type: "agent" as const, name: "query", config: {}, position: { x: 50, y: 200 } },
    { id: "curator_def", type: "agent" as const, name: "curator", config: {}, position: { x: 270, y: 200 } },
    { id: "check_ghi", type: "condition" as const, name: "check", config: { expression: "confidence > 0.5" }, position: { x: 490, y: 200 } },
  ],
  edges: [{ source_id: "query_abc", target_id: "curator_def", label: null, condition: null }],
  enabled: true,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("WorkflowEditor", () => {
  it("renders Save button", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
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
    expect(screen.getByRole("button", { name: "Add Node" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Builder hint — dismissible, persisted in localStorage
// ---------------------------------------------------------------------------

describe("WorkflowEditor — builder hint", () => {
  it("shows the how-to hint on first visit", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.getByText(/build a workflow in three steps/i)).toBeInTheDocument()
  })

  it("dismissing the hint hides it and persists the choice", () => {
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    fireEvent.click(screen.getByRole("button", { name: /dismiss builder hint/i }))
    expect(screen.queryByText(/build a workflow in three steps/i)).not.toBeInTheDocument()
    expect(localStorage.getItem("cerid.workflows.builder-hint-dismissed")).toBe("1")
  })

  it("does not show the hint once dismissed", () => {
    localStorage.setItem("cerid.workflows.builder-hint-dismissed", "1")
    render(<WorkflowEditor workflow={null} onSave={noop} onBack={noop} />)
    expect(screen.queryByText(/build a workflow in three steps/i)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Legend + node detail panel
// ---------------------------------------------------------------------------

describe("WorkflowEditor — legend and node details", () => {
  it("renders the node-type legend under the canvas", () => {
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    expect(screen.getByRole("group", { name: /node type legend/i })).toBeInTheDocument()
    expect(screen.getByText("Node types:")).toBeInTheDocument()
  })

  it("clicking a node opens the detail panel with purpose and data flow", () => {
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    fireEvent.click(screen.getByRole("button", { name: "query (Agent node)" }))

    expect(screen.getByText("Node Details")).toBeInTheDocument()
    expect(screen.getByText("Purpose")).toBeInTheDocument()
    expect(screen.getByText(/retrieves the most relevant knowledge-base entries/i)).toBeInTheDocument()
    expect(screen.getByText("Receives")).toBeInTheDocument()
    expect(screen.getByText("Produces")).toBeInTheDocument()
  })

  it("shows an explicit no-configuration message for unconfigured nodes", () => {
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    fireEvent.click(screen.getByRole("button", { name: "query (Agent node)" }))
    expect(screen.getByText(/no configuration set/i)).toBeInTheDocument()
  })

  it("lists current config values for configured nodes", () => {
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    fireEvent.click(screen.getByRole("button", { name: "check (Condition node)" }))

    // Editable expression input plus the read-only configuration listing
    expect(screen.getByDisplayValue("confidence > 0.5")).toBeInTheDocument()
    expect(screen.getByText("expression")).toBeInTheDocument()
    expect(screen.getByText("confidence > 0.5")).toBeInTheDocument()
    // Config guidance from the catalog
    expect(screen.getByText(/operators: == != > < >= <=/i)).toBeInTheDocument()
  })

  it("is axe-clean with a node selected", async () => {
    const { container } = render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    fireEvent.click(screen.getByRole("button", { name: "query (Agent node)" }))
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("WorkflowEditor — run input (UX-21)", () => {
  it("refuses to run a query pipeline without its input", async () => {
    const { runWorkflow } = await import("@/lib/api")
    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)

    fireEvent.click(screen.getByRole("button", { name: /^run$/i }))

    expect(runWorkflow).not.toHaveBeenCalled()
    expect(screen.getByText(/enter the query/i)).toBeInTheDocument()
  })

  it("runs with the input the user typed, not a hardcoded 'test'", async () => {
    const { runWorkflow } = await import("@/lib/api")
    vi.mocked(runWorkflow).mockResolvedValue({
      id: "run-1",
      workflow_id: "wf-1",
      status: "completed",
      results: { query_abc: { status: "completed", output: { results: [] } } },
      error: null,
      started_at: "2026-08-13T00:00:00Z",
      finished_at: "2026-08-13T00:00:05Z",
    })

    render(<WorkflowEditor workflow={mockWorkflow} onSave={noop} onBack={noop} />)
    fireEvent.change(screen.getByLabelText(/run input/i), {
      target: { value: "what changed this week?" },
    })
    fireEvent.click(screen.getByRole("button", { name: /^run$/i }))

    await waitFor(() =>
      expect(runWorkflow).toHaveBeenCalledWith("wf-1", { query: "what changed this week?" }),
    )
    // The summary tells the user node results are inspectable — the green
    // dots alone left them undiscoverable.
    expect(await screen.findByText(/select a node to inspect its result/i)).toBeInTheDocument()
  })
})
