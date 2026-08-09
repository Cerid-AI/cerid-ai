// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import ExtensionsCategory from "@/components/settings/categories/extensions"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/plugins")) return ok({ plugins: [], total: 0 })
    if (url.includes("/mcp-servers")) return ok({ servers: [], total: 0, total_tools: 0 })
    if (url.includes("/external-apis")) return ok({ adapters: [], total: 0 })
    if (url.includes("/data-sources")) return ok({ sources: [], total: 0 })
    if (url.includes("/settings/pro-automations")) return ok({ automations: [] })
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    return ok({})
  })
}

// One provider registered in BOTH registries (the wikipedia duplicate case)
// plus one registry-exclusive entry each.
function mockApisWithProviders() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/external-apis")) {
      return ok({
        adapters: [
          { slug: "wikipedia", display_name: "Wikipedia", enabled: true, requires_key: false, key_configured: true },
          { slug: "arxiv", display_name: "arXiv", enabled: true, requires_key: false, key_configured: true },
        ],
        total: 2,
      })
    }
    if (url.includes("/data-sources")) {
      return ok({
        sources: [
          { name: "wikipedia", description: "Wikipedia lookups", enabled: false, configured: true, requires_api_key: false, api_key_env_var: "", domains: [] },
          { name: "duckduckgo", description: "Web search", enabled: true, configured: true, requires_api_key: false, api_key_env_var: "", domains: [] },
        ],
        total: 2,
      })
    }
    return mockApis()(url)
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe("ExtensionsCategory — 4-state matrix", () => {
  it("loading: skeleton shown while plugins fetch", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<ExtensionsCategory />, { wrapper })
    expect(document.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThanOrEqual(1)
  })

  it("success: Plugins, MCP Servers, Knowledge Providers sections render", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("Plugins")).toBeInTheDocument()
    expect(screen.getByText("MCP Servers")).toBeInTheDocument()
    // P0-C.4 — the two knowledge registries (external-apis + data-sources)
    // consolidated into a single "Knowledge Providers" section.
    expect(screen.getByText("Knowledge Providers")).toBeInTheDocument()
  })

  it("ST7: renders an intro that distinguishes the four extension types", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText(/Extend Cerid/i)).toBeInTheDocument()
  })

  it("error: plugins fetch failure shows retry link", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/plugins")) {
        return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("err"), json: () => Promise.reject(new Error("err")) })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText(/Retry/i)).toBeInTheDocument()
  })

  it("empty: zero plugins shows empty state", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("Plugins")
    await waitFor(() => {
      expect(screen.queryByText(/Loading/i)).toBeNull()
    })
  })
})

describe("ExtensionsCategory — plugin rows", () => {
  it("shows plugin name and toggle when plugin present", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/plugins")) {
        return ok({
          plugins: [{
            id: "github",
            name: "GitHub",
            description: "GitHub connector",
            enabled: true,
            version: "1.0.0",
            tier_required: "community",
            config_schema: null,
          }],
          total: 1,
        })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("GitHub")).toBeInTheDocument()
  })
})

describe("ExtensionsCategory — MCP servers", () => {
  it("shows Add MCP Server button", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("MCP Servers")
    expect(screen.getByRole("button", { name: /add mcp server/i })).toBeInTheDocument()
  })

  it("shows form fields on add click", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("MCP Servers")
    await user.click(screen.getByRole("button", { name: /add mcp server/i }))
    expect(screen.getByPlaceholderText(/my-server/i)).toBeInTheDocument()
  })

  it("shows MCP server row when server present", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/mcp-servers")) {
        return ok({
          servers: [{
            id: "weather",
            name: "Weather MCP",
            transport: "sse",
            url: "http://localhost:9000/sse",
            env: {},
            headers: {},
            status: "connected",
            tool_count: 3,
          }],
          total: 1,
          total_tools: 3,
        })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("Weather MCP")).toBeInTheDocument()
  })
})

describe("ExtensionsCategory — unified Knowledge Providers (P0-C.4)", () => {
  it("lists entries from BOTH registries with scope badges", async () => {
    vi.stubGlobal("fetch", mockApisWithProviders())
    render(<ExtensionsCategory />, { wrapper })
    // enrichment-only entry (external-apis) + chat-only entry (data-sources)
    expect(await screen.findByText("arXiv")).toBeInTheDocument()
    expect(screen.getByText("duckduckgo")).toBeInTheDocument()
    // scope badges present for both registries
    expect(screen.getAllByText("Enrichment").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("Chat tool").length).toBeGreaterThanOrEqual(1)
  })

  it("renders duplicate slugs (wikipedia) as adjacent rows, one per scope", async () => {
    vi.stubGlobal("fetch", mockApisWithProviders())
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("arXiv")
    // Both wikipedia rows exist with independent toggles.
    expect(screen.getByRole("switch", { name: /wikipedia \(Enrichment\)/i })).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: /wikipedia \(Chat tool\)/i })).toBeInTheDocument()
    // Adjacency: sorted by slug, the two wikipedia rows sit next to each
    // other so the scope difference is self-explanatory.
    const names = screen.getAllByRole("switch")
      .map((el) => el.getAttribute("aria-label") ?? "")
      .filter((l) => /\((Enrichment|Chat tool)\)/.test(l))
    const wikiIdxs = names
      .map((l, i) => (/wikipedia/i.test(l) ? i : -1))
      .filter((i) => i >= 0)
    expect(wikiIdxs).toHaveLength(2)
    expect(wikiIdxs[1] - wikiIdxs[0]).toBe(1)
  })

  it("shows a one-line effect description per scope", async () => {
    vi.stubGlobal("fetch", mockApisWithProviders())
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("arXiv")
    expect(screen.getAllByText(/enriching wiki entities/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/lookup tool when answering/i).length).toBeGreaterThanOrEqual(1)
  })

  it("chat-tool toggle POSTs to /data-sources, enrichment toggle to /external-apis", async () => {
    const fetchMock = mockApisWithProviders()
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("arXiv")

    await user.click(screen.getByRole("switch", { name: /Enable wikipedia \(Chat tool\)/i }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/data-sources/wikipedia/enable"))).toBe(true)
    })

    await user.click(screen.getByRole("switch", { name: /Disable Wikipedia \(Enrichment\)/i }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/external-apis/wikipedia/enabled"))).toBe(true)
    })
  })

  it("error: data-sources failure surfaces the providers retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/data-sources")) return Promise.reject(new Error("network error"))
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText(/Failed to load knowledge providers/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Retry/i).length).toBeGreaterThanOrEqual(1)
  })

  it("is axe-clean with provider rows present", async () => {
    vi.stubGlobal("fetch", mockApisWithProviders())
    const { container } = render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("arXiv")
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("ExtensionsCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("Plugins")
    expect(await axe(container)).toHaveNoViolations()
  })
})
