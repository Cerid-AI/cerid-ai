// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

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
    if (url.includes("/settings/pro-automations")) return ok({ automations: [] })
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    return ok({})
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

  it("success: Plugins, MCP Servers, External Knowledge Providers sections render", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("Plugins")).toBeInTheDocument()
    expect(screen.getByText("MCP Servers")).toBeInTheDocument()
    // ST7 — "External APIs" renamed to the clearer "External Knowledge Providers"
    expect(screen.getByText("External Knowledge Providers")).toBeInTheDocument()
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

describe("ExtensionsCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("Plugins")
    expect(await axe(container)).toHaveNoViolations()
  })
})
