// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import ExtensionsCategory from "@/components/settings/categories/extensions"
import { NavigationProvider } from "@/contexts/navigation-context"

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

  it("ST7: renders an intro that distinguishes the four extension types and links data connections to Sources", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    // GUI spec item 8: the intro draws the plugins-vs-connectors line and
    // routes data-connection seekers to Sources → Connectors in one click.
    expect(await screen.findByText(/Plugins are capability packs/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Sources → Connectors/i })).toBeInTheDocument()
    expect(screen.getByText(/scheduled\s+background automations/i)).toBeInTheDocument()
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

  // GUI spec MUST-6/MUST-7: rows render the manifest's human display_name
  // (raw id demoted to a mono badge), and connector-backing packs cross-link
  // to Sources → Connectors via the sourcesMode deep-link mechanism.
  function connectorPluginFetch() {
    return vi.fn().mockImplementation((url: string) => {
      if (url.includes("/plugins")) {
        return ok({
          plugins: [
            {
              name: "apple_mail",
              display_name: "Apple Mail",
              plugin_type: "connector",
              description: "Apple Mail (.emlx) via signed Swift helper",
              enabled: false,
              version: "0.1.0",
              tier_required: "community",
              config_schema: null,
            },
            {
              name: "ocr",
              display_name: "OCR",
              plugin_type: "parser",
              description: "OCR support for scanned documents",
              enabled: true,
              version: "1.0.0",
              tier_required: "community",
              config_schema: null,
            },
          ],
          total: 2,
        })
      }
      return mockApis()(url)
    })
  }

  it("renders display_name as the row title and the raw name as a mono badge", async () => {
    vi.stubGlobal("fetch", connectorPluginFetch())
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("Apple Mail")).toBeInTheDocument()
    // Raw snake_case id survives as the operator-greppable badge, not the title.
    expect(screen.getByText("apple_mail")).toBeInTheDocument()
    expect(screen.getByText("OCR")).toBeInTheDocument()
    // The switch is labelled with the human name.
    expect(screen.getByRole("switch", { name: /enable apple mail/i })).toBeInTheDocument()
  })

  it("connector-type plugin row cross-links to Sources → Connectors; parser row does not", async () => {
    vi.stubGlobal("fetch", connectorPluginFetch())
    const onPaneChange = vi.fn()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <NavigationProvider activePane="settings" onPaneChange={onPaneChange}>
          <ExtensionsCategory />
        </NavigationProvider>
      </QueryClientProvider>,
    )
    await screen.findByText("Apple Mail")
    // Exactly one plugin-row cross-link: the connector pack's. The intro
    // card has its own Sources → Connectors link, so scope to the effect line.
    const effectLine = screen.getByText(/Backs the Apple Mail connector/i)
    expect(effectLine).toBeInTheDocument()
    expect(screen.queryByText(/Backs the OCR connector/i)).toBeNull()
    const user = userEvent.setup()
    await user.click(within(effectLine).getByRole("button", { name: /Sources → Connectors/i }))
    expect(onPaneChange).toHaveBeenCalledWith("sources")
    expect(new URLSearchParams(window.location.search).get("sources_mode")).toBe("connectors")
  })

  it("falls back to the raw name, without a duplicate badge, when the server predates display_name", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/plugins")) {
        return ok({
          plugins: [{
            name: "apple_mail",
            description: "Apple Mail (.emlx) via signed Swift helper",
            enabled: false,
            version: "0.1.0",
            tier_required: "community",
            config_schema: null,
          }],
          total: 1,
        })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })
    expect(await screen.findByText("apple_mail")).toBeInTheDocument()
    expect(screen.getAllByText("apple_mail")).toHaveLength(1)
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

// ── Spotlight ────────────────────────────────────────────────────────────────
// The section only exists in the desktop app (CoreSpotlight is a host API), and
// it is Pro. Both halves are load-bearing: without the desktop check a browser
// user sees a button that cannot work; without the Pro check `spotlight_donation`
// has no gate anywhere, since the feature never touches the backend.

function installSpotlightBridge() {
  const donate = vi.fn().mockResolvedValue({ ok: true, scanned: 12, donated: 12 })
  const purge = vi.fn().mockResolvedValue({ ok: true })
  ;(window as unknown as { cerid: object }).cerid = {
    appleConnectors: { spotlight: { donate, purge } },
  }
  return { donate, purge }
}

describe("ExtensionsCategory — Spotlight", () => {
  afterEach(() => {
    delete (window as unknown as { cerid?: object }).cerid
  })

  it("is absent in a browser build (no desktop bridge)", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ExtensionsCategory />, { wrapper })
    await screen.findByText("Plugins")
    expect(screen.queryByText("Spotlight")).not.toBeInTheDocument()
  })

  it("community: shows the section but offers no donate button", async () => {
    installSpotlightBridge()
    vi.stubGlobal("fetch", mockApis()) // capabilities → community
    render(<ExtensionsCategory />, { wrapper })

    expect(await screen.findByText("Spotlight")).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Donate to Spotlight/i })).not.toBeInTheDocument()
    })
    expect(screen.getByText(/Requires Pro plan/i)).toBeInTheDocument()
  })

  it("pro: donating calls the bridge and reports what landed", async () => {
    const { donate } = installSpotlightBridge()
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) {
        return ok({ tier: "pro", features: {}, buckets: {} })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })

    const button = await screen.findByRole("button", { name: /Donate to Spotlight/i })
    await userEvent.click(button)

    await waitFor(() => expect(donate).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Donated 12 of 12 artifacts/i)).toBeInTheDocument()
  })

  it("pro: a capped donation is labelled as truncated, not a census (WB-61)", async () => {
    // The main process computed `truncated` all along; the preload type
    // dropped it, so "Donated 5000 of 5000" read as the whole knowledge base.
    installSpotlightBridge()
    ;(window as unknown as { cerid: { appleConnectors: { spotlight: { donate: unknown } } } })
      .cerid.appleConnectors.spotlight.donate = vi.fn().mockResolvedValue({
        ok: true, scanned: 5000, donated: 5000, truncated: true,
      })
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) {
        return ok({ tier: "pro", features: {}, buckets: {} })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    expect(
      await screen.findByText(/truncated at 5,000 — the knowledge base holds more/i),
    ).toBeInTheDocument()
  })

  it("pro: a helper failure surfaces instead of reading as success", async () => {
    installSpotlightBridge()
    ;(window as unknown as { cerid: { appleConnectors: { spotlight: { donate: unknown } } } })
      .cerid.appleConnectors.spotlight.donate = vi.fn().mockResolvedValue({
        ok: false, scanned: 0, donated: 0, error: "ceridspotlight helper not found",
      })
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) {
        return ok({ tier: "pro", features: {}, buckets: {} })
      }
      return mockApis()(url)
    }))
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    expect(await screen.findByText(/ceridspotlight helper not found/i)).toBeInTheDocument()
  })
})

// ── Spotlight retention ──────────────────────────────────────────────────────
// `expiration_days` was in the helper's input schema from the day it was
// written and this UI never sent it, so every donation ever made is permanent —
// KB content stays searchable from Cmd-Space after the artifact is deleted and
// after the Pro entitlement lapses. These pin that the window reaches the
// bridge, because a control that is only stored and never sent looks identical
// to one that works.

const RETENTION_KEY = "cerid-spotlight-retention-days"

function proCapabilities() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/billing/capabilities")) {
      return ok({ tier: "pro", features: {}, buckets: {} })
    }
    return mockApis()(url)
  })
}

describe("ExtensionsCategory — Spotlight retention", () => {
  afterEach(() => {
    delete (window as unknown as { cerid?: object }).cerid
    localStorage.removeItem(RETENTION_KEY)
  })

  it("donates with the 90-day default when nothing has been chosen", async () => {
    const { donate } = installSpotlightBridge()
    vi.stubGlobal("fetch", proCapabilities())
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    await waitFor(() =>
      expect(donate).toHaveBeenCalledWith(expect.objectContaining({ expiration_days: 90 })),
    )
  })

  it("sends the stored window, and persists a change to it", async () => {
    const { donate } = installSpotlightBridge()
    vi.stubGlobal("fetch", proCapabilities())
    render(<ExtensionsCategory />, { wrapper })

    const user = userEvent.setup()
    await user.click(await screen.findByRole("combobox", { name: /Spotlight retention/i }))
    await user.click(await screen.findByRole("option", { name: "30 days" }))

    expect(localStorage.getItem(RETENTION_KEY)).toBe("30")
    await user.click(screen.getByRole("button", { name: /Donate to Spotlight/i }))
    await waitFor(() =>
      expect(donate).toHaveBeenCalledWith(expect.objectContaining({ expiration_days: 30 })),
    )
  })

  it("sends 0 for 'never expire' — an explicit choice, not an absent field", async () => {
    localStorage.setItem(RETENTION_KEY, "0")
    const { donate } = installSpotlightBridge()
    vi.stubGlobal("fetch", proCapabilities())
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    await waitFor(() =>
      expect(donate).toHaveBeenCalledWith(expect.objectContaining({ expiration_days: 0 })),
    )
  })

  it("falls back to the default when the stored value is not an offered option", async () => {
    // The failure that matters: a junk key must not resolve to "never expire",
    // which is the longest window rather than the safest one.
    localStorage.setItem(RETENTION_KEY, "forever")
    const { donate } = installSpotlightBridge()
    vi.stubGlobal("fetch", proCapabilities())
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    await waitFor(() =>
      expect(donate).toHaveBeenCalledWith(expect.objectContaining({ expiration_days: 90 })),
    )
  })

  it("reports the window the main process applied, not the one requested", async () => {
    // donateKnowledgeBase normalises the request, so the two can differ. Echoing
    // the request back would tell the operator their choice took effect when it
    // did not.
    installSpotlightBridge()
    ;(window as unknown as { cerid: { appleConnectors: { spotlight: { donate: unknown } } } })
      .cerid.appleConnectors.spotlight.donate = vi.fn().mockResolvedValue({
        ok: true, scanned: 12, donated: 12, expiration_days: 90,
      })
    localStorage.setItem(RETENTION_KEY, "0")
    vi.stubGlobal("fetch", proCapabilities())
    render(<ExtensionsCategory />, { wrapper })

    await userEvent.click(await screen.findByRole("button", { name: /Donate to Spotlight/i }))
    expect(await screen.findByText(/expire after 90 days/i)).toBeInTheDocument()
  })
})

// ── Plugin entitlements — per-FLAG, loading/error-aware ──────────────────────
// Rows used to key entirely off `tier_required`, so a flag the server granted
// below Pro still rendered locked, and a failed/in-flight capabilities fetch
// rendered as a settled community verdict with an upgrade pitch.

describe("ExtensionsCategory — plugin rows resolve entitlement per flag", () => {
  function meetingPlugin() {
    return {
      plugins: [{
        name: "meeting_capture",
        description: "Meeting capture",
        enabled: false,
        version: "1.0.0",
        tier_required: "pro",
        config_schema: null,
        file_types: [],
        capabilities: [],
        feature_flags: ["meeting_diarization"],
      }],
      total: 1,
    }
  }

  function stubFetch(capabilities: () => Promise<unknown>) {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) return capabilities()
      if (url.includes("/plugins")) return ok(meetingPlugin())
      return mockApis()(url)
    }))
  }

  it("a flag the server granted below Pro unlocks the row despite tier_required", async () => {
    stubFetch(() => ok({
      tier: "community",
      features: { meeting_diarization: { enabled: true, tier_required: "community" } },
      buckets: {},
    }))
    render(<ExtensionsCategory />, { wrapper })

    const sw = await screen.findByRole("switch", { name: /enable meeting_capture/i })
    await waitFor(() => expect(sw).toBeEnabled())
    expect(screen.queryByText(/requires pro plan/i)).toBeNull()
  })

  it("a locked flag disables the row with the upgrade path", async () => {
    stubFetch(() => ok({
      tier: "community",
      features: { meeting_diarization: { enabled: false, tier_required: "pro" } },
      buckets: {},
    }))
    render(<ExtensionsCategory />, { wrapper })

    const sw = await screen.findByRole("switch", { name: /enable meeting_capture/i })
    await waitFor(() => expect(sw).toBeDisabled())
    expect(screen.getByText(/requires pro plan/i)).toBeInTheDocument()
  })

  it("a failed capabilities fetch stays locked but is not sold as a community verdict", async () => {
    stubFetch(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.reject(new Error("boom")),
      text: () => Promise.resolve("boom"),
    }))
    render(<ExtensionsCategory />, { wrapper })

    const sw = await screen.findByRole("switch", { name: /enable meeting_capture/i })
    await waitFor(() => expect(sw).toBeDisabled())
    // Fail closed, but say the plan is unverified instead of pitching a trial.
    // (Matches in both the plugin row and the automations section — every
    // locked surface on the page must switch to the unverified copy.)
    expect((await screen.findAllByText(/couldn.t verify your plan/i)).length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/start a free trial/i)).toBeNull()
  })

  it("no verdict renders while capabilities are in flight", async () => {
    stubFetch(() => new Promise(() => {}))
    render(<ExtensionsCategory />, { wrapper })

    const sw = await screen.findByRole("switch", { name: /enable meeting_capture/i })
    expect(sw).toBeDisabled() // inert until the verdict settles — never a pitch
    expect(screen.queryByText(/requires pro plan/i)).toBeNull()
    expect(screen.queryByText(/start a free trial/i)).toBeNull()
  })
})
