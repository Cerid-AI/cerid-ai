// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"

vi.mock("@/lib/api/connectors", () => ({
  startConnectorAuth: vi.fn(),
  getConnectorAuthStatus: vi.fn(),
  disconnectConnector: vi.fn(),
}))

import {
  startConnectorAuth,
  getConnectorAuthStatus,
  disconnectConnector,
} from "@/lib/api/connectors"
import type { ConnectorStatus } from "@/lib/api/connectors"
import { ConnectorDetail } from "@/components/sources/connector-detail"

const mockStart = startConnectorAuth as ReturnType<typeof vi.fn>
const mockStatus = getConnectorAuthStatus as ReturnType<typeof vi.fn>
const mockDisconnect = disconnectConnector as ReturnType<typeof vi.fn>

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function makeConnector(overrides: Partial<ConnectorStatus> = {}): ConnectorStatus {
  return {
    slug: "google_drive",
    display_name: "Google Drive",
    feature_flag: "CONNECTOR_GOOGLE_DRIVE",
    feature_enabled: true,
    env_complete: true,
    missing_env: [],
    data_source_registered: false,
    data_source_configured: false,
    sibling_reachable: null,
    sibling_circuit_open: null,
    auth_kind: "google_oauth",
    instruction_doc: "docs/connectors/google_drive.md",
    ...overrides,
  }
}

describe("ConnectorDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the connector display name in the dialog title", () => {
    render(
      <ConnectorDetail connector={makeConnector()} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("Google Drive")).toBeInTheDocument()
  })

  it("shows env_incomplete state with missing_env list and instruction_doc", () => {
    const connector = makeConnector({
      env_complete: false,
      missing_env: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    })
    render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("GOOGLE_CLIENT_ID")).toBeInTheDocument()
    expect(screen.getByText("GOOGLE_CLIENT_SECRET")).toBeInTheDocument()
    expect(screen.getByText("docs/connectors/google_drive.md")).toBeInTheDocument()
  })

  it("shows Connect button for not-configured connector and calls startConnectorAuth on click", async () => {
    mockStart.mockResolvedValue({
      auth_kind: "google_oauth",
      auth_url: "https://accounts.google.com/o/oauth2/auth?state=abc",
      device_code: null,
      verification_uri: null,
      expires_in: 600,
      settings_url: null,
      instructions: "Open the link and authorise Cerid AI.",
    })
    mockStatus.mockResolvedValue({ slug: "google_drive", completed: false, detail: "pending" })

    render(
      <ConnectorDetail connector={makeConnector()} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )

    const connectBtn = screen.getByRole("button", { name: /connect/i })
    fireEvent.click(connectBtn)

    await waitFor(() => expect(mockStart).toHaveBeenCalledWith("google_drive"))
    expect(await screen.findByText("Open the link and authorise Cerid AI.")).toBeInTheDocument()
    expect(screen.getByText("https://accounts.google.com/o/oauth2/auth?state=abc")).toBeInTheDocument()
  })

  it("renders device_code flow (msal) after startConnectorAuth", async () => {
    mockStart.mockResolvedValue({
      auth_kind: "msal",
      auth_url: null,
      device_code: "ABCD-EFGH",
      verification_uri: "https://microsoft.com/devicelogin",
      expires_in: 900,
      settings_url: null,
      instructions: "Enter the code at the link below.",
    })
    mockStatus.mockResolvedValue({ slug: "onedrive", completed: false, detail: "pending" })

    const connector = makeConnector({ slug: "onedrive", display_name: "OneDrive", auth_kind: "msal" })
    render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )

    fireEvent.click(screen.getByRole("button", { name: /connect/i }))
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument()
    expect(screen.getByText("https://microsoft.com/devicelogin")).toBeInTheDocument()
  })

  it("renders settings_url flow (tcc) after startConnectorAuth", async () => {
    mockStart.mockResolvedValue({
      auth_kind: "tcc",
      auth_url: null,
      device_code: null,
      verification_uri: null,
      expires_in: null,
      settings_url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
      instructions: "Open System Settings and grant Calendar access.",
    })
    mockStatus.mockResolvedValue({ slug: "apple_calendar", completed: false, detail: "pending" })

    const connector = makeConnector({ slug: "apple_calendar", display_name: "Apple Calendar", auth_kind: "tcc" })
    render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )

    fireEvent.click(screen.getByRole("button", { name: /connect/i }))
    expect(await screen.findByText("Open System Settings and grant Calendar access.")).toBeInTheDocument()
    expect(screen.getByText("x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars")).toBeInTheDocument()
  })

  it("polls getConnectorAuthStatus every 3s and stops on completed", async () => {
    vi.useFakeTimers()
    const onClose = vi.fn()
    mockStart.mockResolvedValue({
      auth_kind: "google_oauth",
      auth_url: "https://accounts.google.com/auth",
      device_code: null,
      verification_uri: null,
      expires_in: 600,
      settings_url: null,
      instructions: "Follow the link.",
    })
    // First poll: not done; second: completed
    mockStatus
      .mockResolvedValueOnce({ slug: "google_drive", completed: false, detail: "pending" })
      .mockResolvedValue({ slug: "google_drive", completed: true, detail: "Connected!" })

    render(
      <ConnectorDetail connector={makeConnector()} open onClose={onClose} />,
      { wrapper: wrap() },
    )

    fireEvent.click(screen.getByRole("button", { name: /connect/i }))

    // Wait for startConnectorAuth to complete (drains promise microtasks)
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })

    // Advance 3s → first poll fires
    await act(async () => { vi.advanceTimersByTime(3000) })
    // Allow the promise from mockStatus to resolve
    await act(async () => { await Promise.resolve() })
    expect(mockStatus).toHaveBeenCalledTimes(1)

    // Advance another 3s → second poll fires (completed: true)
    await act(async () => { vi.advanceTimersByTime(3000) })
    await act(async () => { await Promise.resolve() })
    expect(mockStatus).toHaveBeenCalledTimes(2)

    // onClose should have been called after completed
    expect(onClose).toHaveBeenCalled()

    vi.useRealTimers()
  })

  it("shows Disconnect button for configured connector and shows detail on click", async () => {
    mockDisconnect.mockResolvedValue({
      slug: "google_drive",
      cleared: false,
      detail: "Revoke access manually at myaccount.google.com/permissions.",
    })

    const connector = makeConnector({ data_source_configured: true })
    render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )

    const disconnectBtn = screen.getByRole("button", { name: /disconnect/i })
    fireEvent.click(disconnectBtn)

    await waitFor(() => expect(mockDisconnect).toHaveBeenCalledWith("google_drive"))
    expect(
      await screen.findByText("Revoke access manually at myaccount.google.com/permissions."),
    ).toBeInTheDocument()
  })

  it("renders the per-connector explainer block when the backend provides it (P0-C.4)", () => {
    const connector = {
      ...makeConnector(),
      imports_desc: "Files and docs matching your queries.",
      sync_semantics: "On-demand lookup while connected — no one-time import.",
      lands_in: "Chat answers with citations.",
    }
    render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("How this connector works")).toBeInTheDocument()
    expect(screen.getByText("Reads")).toBeInTheDocument()
    expect(screen.getByText("Files and docs matching your queries.")).toBeInTheDocument()
    expect(screen.getByText("Sync")).toBeInTheDocument()
    expect(screen.getByText("On-demand lookup while connected — no one-time import.")).toBeInTheDocument()
    expect(screen.getByText("Data destination")).toBeInTheDocument()
    expect(screen.getByText("Chat answers with citations.")).toBeInTheDocument()
  })

  it("omits the explainer block for older payloads without the fields", () => {
    render(
      <ConnectorDetail connector={makeConnector()} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(screen.queryByText("How this connector works")).not.toBeInTheDocument()
  })

  it("is axe-clean with the explainer block present", async () => {
    const connector = {
      ...makeConnector(),
      imports_desc: "Files and docs matching your queries.",
      sync_semantics: "On-demand lookup while connected.",
      lands_in: "Chat answers with citations.",
    }
    const { container } = render(
      <ConnectorDetail connector={connector} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for not-configured connector", async () => {
    const { container } = render(
      <ConnectorDetail connector={makeConnector()} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for configured connector", async () => {
    const { container } = render(
      <ConnectorDetail connector={makeConnector({ data_source_configured: true })} open onClose={vi.fn()} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ── Sibling reachability ─────────────────────────────────────────────────────
// `sibling_reachable: null` carries two meanings — "no sibling needed" (the
// Apple/TCC connectors) and "needed, but never contacted". The row used to be
// hidden on null, so the second case rendered as silence and the operator read
// silence as success. `requires_sibling` is what tells them apart.

describe("ConnectorDetail — sibling reachability", () => {
  it("hides the row when the connector has no sibling at all", () => {
    render(
      <ConnectorDetail
        connector={makeConnector({ requires_sibling: null, sibling_reachable: null })}
        open
        onClose={vi.fn()}
      />,
      { wrapper: wrap() },
    )
    expect(screen.queryByText(/Sibling service/i)).not.toBeInTheDocument()
  })

  it("says 'not contacted yet' rather than hiding it, when one IS required", () => {
    render(
      <ConnectorDetail
        connector={makeConnector({ requires_sibling: "ms365", sibling_reachable: null })}
        open
        onClose={vi.fn()}
      />,
      { wrapper: wrap() },
    )
    expect(screen.getByText(/Sibling service \(ms365\) — not contacted yet/i)).toBeInTheDocument()
  })

  it("reports plain reachability once a call has succeeded", () => {
    render(
      <ConnectorDetail
        connector={makeConnector({ requires_sibling: "ms365", sibling_reachable: true })}
        open
        onClose={vi.fn()}
      />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("Sibling service reachable")).toBeInTheDocument()
  })
})
