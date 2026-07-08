// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"

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

// ---------------------------------------------------------------------------
// D.2: four-state matrix — loading (Skeleton) / error (PaneError)
// ---------------------------------------------------------------------------

describe("WorkflowList — four-state matrix (D.2)", () => {
  it("loading: shows Skeleton rows while fetching", () => {
    vi.mocked(fetchWorkflows).mockReturnValue(new Promise(() => {})) // never resolves
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    expect(screen.getByRole("status", { name: /loading workflows/i })).toBeInTheDocument()
  })

  it("error: shows PaneError (destructive Alert) with Retry on fetch failure", async () => {
    vi.mocked(fetchWorkflows).mockRejectedValue(new Error("Connection refused"))
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await waitFor(() => {
      expect(screen.getByText(/failed to load workflows/i)).toBeInTheDocument()
    })
    expect(screen.getByText("Connection refused")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("error: Retry re-invokes the fetch", async () => {
    vi.mocked(fetchWorkflows).mockRejectedValueOnce(new Error("Connection refused"))
    vi.mocked(fetchWorkflows).mockResolvedValueOnce({ workflows: mockWorkflows, total: 2 })
    const user = userEvent.setup()
    render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await screen.findByRole("button", { name: /retry/i })
    await user.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByText("Ingestion Pipeline")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("WorkflowList — axe-clean (D.3)", () => {
  it("is axe-clean in loading state", async () => {
    vi.mocked(fetchWorkflows).mockReturnValue(new Promise(() => {}))
    const { container } = render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    vi.mocked(fetchWorkflows).mockRejectedValue(new Error("fail"))
    const { container } = render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await waitFor(() => screen.getByText(/failed to load workflows/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    vi.mocked(fetchWorkflows).mockResolvedValue({ workflows: [], total: 0 })
    const { container } = render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await screen.findByText(/no workflows yet/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in success (populated) state", async () => {
    vi.mocked(fetchWorkflows).mockResolvedValue({ workflows: mockWorkflows, total: 2 })
    const { container } = render(<WorkflowList onEdit={noop} onCreate={noop} onDuplicate={noop} />)
    await screen.findByText("Ingestion Pipeline")
    expect(await axe(container)).toHaveNoViolations()
  })
})
