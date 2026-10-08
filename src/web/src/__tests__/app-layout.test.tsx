// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ConversationsProvider } from "@/contexts/conversations-context"

if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

const fetchHealthStatus = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api", () => ({
  fetchHealthStatus,
  retestServices: vi.fn().mockResolvedValue(undefined),
  fetchModelUpdatesFull: vi.fn().mockResolvedValue({ updates: [] }),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  fetchForgottenConversations: vi.fn().mockResolvedValue({ items: [], cursor: null }),
  ConversationGoneError: class ConversationGoneError extends Error { id = "" },
  syncConversation: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("@/lib/api/settings", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ version: "1.0.0" }),
  applyModelUpdates: vi.fn(),
}))
vi.mock("@/hooks/use-agent-console", () => ({
  useAgentConsole: () => ({ events: [], connected: false, unreadCount: 0, clearEvents: vi.fn(), resetUnread: vi.fn() }),
}))
vi.mock("@/components/layout/deep-link-router", () => ({ DeepLinkRouter: () => null }))
vi.mock("@/components/layout/status-bar", () => ({ StatusBar: () => null }))
vi.mock("@/components/model-download-banner", () => ({
  ModelDownloadBanner: () => <div data-testid="model-download-banner" />,
}))

import { AppLayout } from "@/components/layout/app-layout"

function renderShell(initialPane: "chat" | "settings" = "settings") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <ConversationsProvider>
        <AppLayout initialPane={initialPane}>
          {(pane) => <button type="button">{`${pane} pane content`}</button>}
        </AppLayout>
      </ConversationsProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  fetchHealthStatus.mockReset()
})

describe("AppLayout — server unreachable banner", () => {
  it("is shown on a pane other than chat", async () => {
    fetchHealthStatus.mockRejectedValue(new Error("network down"))
    renderShell("settings")
    const banner = await screen.findByRole("alert")
    expect(banner).toHaveTextContent("Unable to reach the server")
    expect(screen.getAllByRole("alert")).toHaveLength(1)
  })

  it("sits above the shell row, beside the model download banner, not inside the clipped row", async () => {
    fetchHealthStatus.mockRejectedValue(new Error("network down"))
    renderShell("settings")
    const banner = await screen.findByRole("alert")
    const row = screen.getByRole("main").parentElement!
    expect(row).not.toContainElement(banner)
    expect(banner.parentElement).toBe(screen.getByTestId("model-download-banner").parentElement)
    expect(banner.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("shows nothing while the server is healthy", async () => {
    fetchHealthStatus.mockResolvedValue({ degradation_tier: "full" })
    renderShell("settings")
    await vi.waitFor(() => expect(fetchHealthStatus).toHaveBeenCalled())
    expect(screen.queryByRole("alert")).toBeNull()
  })
})

describe("AppLayout — skip link", () => {
  beforeEach(() => {
    fetchHealthStatus.mockResolvedValue({ degradation_tier: "full" })
  })

  it("is the first thing the Tab key reaches", async () => {
    const user = userEvent.setup()
    renderShell("settings")
    await user.tab()
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveFocus()
  })

  it("is hidden until it has focus", () => {
    renderShell("settings")
    const link = screen.getByRole("link", { name: "Skip to main content" })
    expect(link).toHaveClass("sr-only")
    expect(link).toHaveClass("focus-visible:not-sr-only")
  })

  it("moves focus to the main region, past the sidebar", async () => {
    const user = userEvent.setup()
    renderShell("settings")
    const link = screen.getByRole("link", { name: "Skip to main content" })
    const main = screen.getByRole("main")
    expect(link).toHaveAttribute("href", `#${main.id}`)
    expect(main.id).not.toBe("")

    await user.tab()
    await user.keyboard("{Enter}")
    expect(main).toHaveFocus()

    await user.tab()
    expect(screen.getByRole("button", { name: "settings pane content" })).toHaveFocus()
  })
})
