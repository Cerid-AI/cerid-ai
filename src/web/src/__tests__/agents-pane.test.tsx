// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

// The four-state matrix for the Agents pane lives in CustomAgentsPane — the
// parent AgentsPane is a thin Tabs wrapper around AgentCards + this pane + the
// streaming AgentConsole. Loading / Error+retry / Empty / Success are all
// produced here, so the matrix is asserted against this unit.
vi.mock("@/lib/api/custom-agents", () => ({
  listCustomAgents: vi.fn(),
  listAgentTemplates: vi.fn(),
  createAgentFromTemplate: vi.fn(),
  deleteCustomAgent: vi.fn(),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchSettings: vi.fn(),
}))

import {
  listCustomAgents,
  listAgentTemplates,
  type CustomAgentDefinition,
  type AgentTemplate,
} from "@/lib/api/custom-agents"
import { fetchSettings } from "@/lib/api/settings"
import CustomAgentsPane from "@/components/agents/custom-agents-pane"

const mockListCustomAgents = listCustomAgents as ReturnType<typeof vi.fn>
const mockListAgentTemplates = listAgentTemplates as ReturnType<typeof vi.fn>
const mockFetchSettings = fetchSettings as ReturnType<typeof vi.fn>

const TEMPLATES: AgentTemplate[] = [
  {
    template_id: "research-assistant",
    name: "Research Assistant",
    description: "Multi-source research with citations",
    system_prompt: "You are a research assistant.",
  },
  {
    template_id: "code-reviewer",
    name: "Code Reviewer",
    description: "Reviews diffs for correctness",
    system_prompt: "You are a code reviewer.",
  },
]

function makeAgent(overrides: Partial<CustomAgentDefinition> = {}): CustomAgentDefinition {
  return {
    agent_id: `agent-${Math.random().toString(36).slice(2, 8)}`,
    name: "My Research Agent",
    description: "Custom research agent",
    system_prompt: "You are helpful.",
    template_id: "research-assistant",
    rag_mode: "smart",
    domains: ["research"],
    tools: ["kb_search"],
    ...overrides,
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  // Default: feature enabled, agents + templates load successfully (empty list).
  mockFetchSettings.mockResolvedValue({ strict_agents_only: false })
  mockListCustomAgents.mockResolvedValue({ agents: [], total: 0 })
  mockListAgentTemplates.mockResolvedValue({ templates: TEMPLATES })
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("CustomAgentsPane — four-state matrix (D.2)", () => {
  it("idle/loading: shows Skeleton placeholders while fetching", () => {
    mockListCustomAgents.mockReturnValue(new Promise(() => {})) // never resolves
    const { container } = render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    const skeletons = container.querySelectorAll("[data-slot=skeleton], [role=status]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("loaded: renders custom agent rows after data arrives", async () => {
    mockListCustomAgents.mockResolvedValue({
      agents: [makeAgent({ name: "My Research Agent" })],
      total: 1,
    })
    render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText("My Research Agent")).toBeInTheDocument()
  })

  it("empty: shows empty state when no custom agents exist", async () => {
    mockListCustomAgents.mockResolvedValue({ agents: [], total: 0 })
    render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    expect(await screen.findByText(/No custom agents yet/i)).toBeInTheDocument()
  })

  it("error: shows destructive Alert with Retry button on fetch failure", async () => {
    mockListCustomAgents.mockRejectedValue(new Error("Connection refused"))
    render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Failed to load custom agents/i)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("error: Retry re-issues the fetch", async () => {
    mockListCustomAgents.mockRejectedValueOnce(new Error("boom"))
    render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    const retry = await screen.findByRole("button", { name: /retry/i })
    // Second attempt succeeds with one agent.
    mockListCustomAgents.mockResolvedValue({
      agents: [makeAgent({ name: "Recovered Agent" })],
      total: 1,
    })
    fireEvent.click(retry)
    expect(await screen.findByText("Recovered Agent")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// STRICT_AGENTS_ONLY guard
// ---------------------------------------------------------------------------

describe("CustomAgentsPane — strict mode", () => {
  it("renders the disabled banner when strict_agents_only is true", async () => {
    mockFetchSettings.mockResolvedValue({ strict_agents_only: true })
    render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    expect(
      await screen.findByText(/Custom agents are disabled in this deployment/i),
    ).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("CustomAgentsPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in loading state", async () => {
    mockListCustomAgents.mockReturnValue(new Promise(() => {}))
    const { container } = render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in empty state", async () => {
    mockListCustomAgents.mockResolvedValue({ agents: [], total: 0 })
    const { container } = render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    await screen.findByText(/No custom agents yet/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in populated state", async () => {
    mockListCustomAgents.mockResolvedValue({
      agents: [makeAgent({ name: "My Research Agent" })],
      total: 1,
    })
    const { container } = render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    await screen.findByText("My Research Agent")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    mockListCustomAgents.mockRejectedValue(new Error("fail"))
    const { container } = render(<CustomAgentsPane />, { wrapper: makeWrapper() })
    await screen.findByText(/Failed to load custom agents/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
