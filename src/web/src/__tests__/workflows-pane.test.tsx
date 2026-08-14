// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { Workflow } from "@/lib/types"

vi.mock("@/lib/api", async () => {
  const { FALLBACK_NODE_CATALOG } = await import("@/components/workflows/node-catalog")
  return {
    fetchWorkflows: vi.fn(),
    deleteWorkflow: vi.fn(),
    fetchWorkflowRuns: vi.fn().mockResolvedValue([]),
    createWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    runWorkflow: vi.fn(),
    fetchWorkflowTemplates: vi.fn().mockResolvedValue([]),
    fetchWorkflowNodeTypes: vi.fn().mockResolvedValue(FALLBACK_NODE_CATALOG),
  }
})

import { fetchWorkflows } from "@/lib/api"
import WorkflowsPane from "@/components/workflows/workflows-pane"

const mockFetchWorkflows = fetchWorkflows as ReturnType<typeof vi.fn>

const mockWorkflow: Workflow = {
  id: "wf-1",
  name: "Ingestion Pipeline",
  description: "Processes new documents",
  nodes: [{ id: "n1", type: "agent", name: "curator", config: {}, position: { x: 0, y: 0 } }],
  edges: [],
  enabled: true,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockFetchWorkflows.mockResolvedValue({ workflows: [mockWorkflow], total: 1 })
})

describe("WorkflowsPane — list <-> editor navigation", () => {
  it("renders the workflow list by default", async () => {
    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText("Ingestion Pipeline")).toBeInTheDocument()
  })

  it("clicking Edit switches to the editor", async () => {
    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    fireEvent.click(screen.getByRole("button", { name: "Edit workflow" }))
    expect(await screen.findByDisplayValue("Ingestion Pipeline")).toBeInTheDocument()
  })

  it("Back returns to the list and refreshes it", async () => {
    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    mockFetchWorkflows.mockClear() // isolate the post-Back refetch from the initial mount fetch

    fireEvent.click(screen.getByRole("button", { name: "Edit workflow" }))
    await screen.findByDisplayValue("Ingestion Pipeline")

    fireEvent.click(screen.getByRole("button", { name: /back/i }))

    expect(await screen.findByText("Ingestion Pipeline")).toBeInTheDocument()
    await waitFor(() => expect(mockFetchWorkflows).toHaveBeenCalledTimes(1))
  })

  it("Save stays in the builder instead of exiting to the list (UX-21)", async () => {
    const { updateWorkflow } = await import("@/lib/api")
    vi.mocked(updateWorkflow).mockResolvedValue({ ...mockWorkflow, name: "Ingestion Pipeline" })

    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    fireEvent.click(screen.getByRole("button", { name: "Edit workflow" }))
    await screen.findByDisplayValue("Ingestion Pipeline")

    fireEvent.click(screen.getByRole("button", { name: /save/i }))

    // Still in the builder: the canvas toolbar is present, the list header is not.
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalled())
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /new workflow/i })).not.toBeInTheDocument()
  })

  it("New Workflow opens a blank editor", async () => {
    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    fireEvent.click(screen.getByRole("button", { name: /new workflow/i }))
    expect(await screen.findByPlaceholderText("Workflow name...")).toHaveValue("")
  })

  it("Duplicate seeds the editor with a copy (blank id, name suffixed)", async () => {
    render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    fireEvent.click(screen.getByRole("button", { name: "Duplicate workflow" }))
    expect(await screen.findByDisplayValue("Ingestion Pipeline (copy)")).toBeInTheDocument()
  })
})

describe("WorkflowsPane — axe-clean", () => {
  it("is axe-clean in list state", async () => {
    const { container } = render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in editor state", async () => {
    const { container } = render(<WorkflowsPane />, { wrapper: makeWrapper() })
    await screen.findByText("Ingestion Pipeline")
    fireEvent.click(screen.getByRole("button", { name: "Edit workflow" }))
    await screen.findByDisplayValue("Ingestion Pipeline")
    expect(await axe(container)).toHaveNoViolations()
  })
})
