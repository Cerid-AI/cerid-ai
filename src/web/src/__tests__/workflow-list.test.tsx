// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchWorkflows: vi.fn(),
  deleteWorkflow: vi.fn(),
  fetchWorkflowRuns: vi.fn().mockResolvedValue([]),
}))

import { fetchWorkflows } from "@/lib/api"
import WorkflowList from "@/components/workflows/workflow-list"
import type { Workflow } from "@/lib/types"

const noop = () => {}

const mockWorkflows: Workflow[] = [
  {
    id: "wf-1",
    name: "Ingestion Pipeline",
    description: "Processes new documents",
    nodes: [{ id: "n1", type: "agent", name: "curator", config: {}, position: { x: 0, y: 0 } }],
    edges: [],
    enabled: true,
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
  {
    id: "wf-2",
    name: "Review Pipeline",
    description: "Reviews knowledge quality",
    nodes: [
      { id: "n1", type: "agent", name: "audit", config: {}, position: { x: 0, y: 0 } },
      { id: "n2", type: "agent", name: "rectify", config: {}, position: { x: 200, y: 0 } },
    ],
    edges: [{ source_id: "n1", target_id: "n2", label: null, condition: null }],
    enabled: false,
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
]

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("WorkflowList", () => {
  it("renders workflow cards after loading", async () => {
    vi.mocked(fetchWorkflows).mockResolvedValue({ workflows: mockWorkflows, total: 2 })
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    expect(await screen.findByText("Ingestion Pipeline")).toBeInTheDocument()
    expect(screen.getByText("Review Pipeline")).toBeInTheDocument()
  })

  it("shows node and edge counts", async () => {
    vi.mocked(fetchWorkflows).mockResolvedValue({ workflows: mockWorkflows, total: 2 })
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await screen.findByText("Ingestion Pipeline")
    expect(screen.getByText("1 nodes")).toBeInTheDocument()
    expect(screen.getByText("1 edges")).toBeInTheDocument()
  })

  it("shows disabled badge for disabled workflows", async () => {
    vi.mocked(fetchWorkflows).mockResolvedValue({ workflows: mockWorkflows, total: 2 })
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await screen.findByText("Ingestion Pipeline")
    expect(screen.getByText("disabled")).toBeInTheDocument()
  })
})
