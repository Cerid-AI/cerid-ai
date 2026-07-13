// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"

vi.mock("@/lib/api/sources", () => ({
  listIngestionSources: vi.fn(),
  listSourceKinds: vi.fn(),
  INGESTION_KINDS: [],
}))
vi.mock("@/lib/api/connectors", () => ({
  listConnectors: vi.fn(),
  startConnectorAuth: vi.fn(),
  getConnectorAuthStatus: vi.fn(),
  disconnectConnector: vi.fn(),
}))
vi.mock("@/lib/api/email", () => ({
  fetchEmailStatus: vi.fn(),
  configureEmail: vi.fn(),
  pollEmailNow: vi.fn(),
  deleteEmailSource: vi.fn(),
}))
import { listIngestionSources, listSourceKinds } from "@/lib/api/sources"
import { listConnectors } from "@/lib/api/connectors"
import { fetchEmailStatus } from "@/lib/api/email"
import { SourcesConnectors } from "@/components/sources/sources-connectors"

const mockSources = listIngestionSources as ReturnType<typeof vi.fn>
const mockConnectors = listConnectors as ReturnType<typeof vi.fn>
const mockFetchEmailStatus = fetchEmailStatus as ReturnType<typeof vi.fn>
const mockListSourceKinds = listSourceKinds as ReturnType<typeof vi.fn>

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockSources.mockResolvedValue([
    { id: "folder:1", kind: "folder", display_name: "Notes", status: "connected", config: { path: "/n" } },
  ])
  mockConnectors.mockResolvedValue([
    {
      slug: "gmail",
      display_name: "Gmail",
      feature_flag: "CONNECTOR_GMAIL",
      feature_enabled: true,
      env_complete: true,
      missing_env: [],
      data_source_registered: false,
      data_source_configured: false,
      sibling_reachable: null,
      sibling_circuit_open: null,
      auth_kind: "oauth",
      instruction_doc: "",
      imports_desc: "Email messages matching your queries.",
      sync_semantics: "On-demand lookup while connected — no bulk import.",
      lands_in: "Chat answers with citations.",
    },
  ])
  mockFetchEmailStatus.mockResolvedValue({ last_poll: null, messages_ingested: 0, errors: [] })
})

describe("SourcesConnectors", () => {
  it("lists ingestion sources from /sources", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    expect(await screen.findByText("Notes")).toBeInTheDocument()
  })

  it("renders connector rows alongside source rows", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    expect(await screen.findByText("Notes")).toBeInTheDocument()
    // Gmail connector row: displayName rendered as the row title.
    expect(screen.getAllByText("Gmail").length).toBeGreaterThanOrEqual(1)
  })

  it("connector rows show their sync-semantics explainer as the sub-label (P0-C.4)", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    expect(
      screen.getByText("On-demand lookup while connected — no bulk import."),
    ).toBeInTheDocument()
  })

  it("does not render external-API or plugin sections", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    expect(screen.queryByText(/external api/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/plugin/i)).not.toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("selecting a connector row opens ConnectorDetail (not SourceDetailPane)", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    // Wait for both rows to load
    expect(await screen.findByText("Notes")).toBeInTheDocument()
    const gmailButtons = screen.getAllByText("Gmail")
    // Click the first Gmail element (row select button contains the text)
    fireEvent.click(gmailButtons[0])
    // ConnectorDetail dialog shows the connector's display_name in its header
    // SourceDetailPane would show source-specific content (retention, quality-floor, etc.)
    // ConnectorDetail shows "Status" section — not present for source rows
    expect(screen.getByText("Status")).toBeInTheDocument()
    // SourceDetailPane would show "Policy" section — connector detail does NOT
    expect(screen.queryByText("Policy")).not.toBeInTheDocument()
  })

  it("renders an Email (IMAP) row when fetchEmailStatus returns a status", async () => {
    mockFetchEmailStatus.mockResolvedValue({ configured: true, last_poll: null, messages_ingested: 0, errors: [] })
    render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    // The row title is in a span with title="Email (IMAP)"
    expect(await screen.findByTitle("Email (IMAP)")).toBeInTheDocument()
  })

  it("selecting the Email (IMAP) row opens EmailDetail (not the old inline section)", async () => {
    mockFetchEmailStatus.mockResolvedValue({ configured: true, last_poll: null, messages_ingested: 0, errors: [] })
    render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    const emailRowTitle = await screen.findByTitle("Email (IMAP)")
    fireEvent.click(emailRowTitle)
    // EmailDetail dialog renders the IMAP form
    expect(await screen.findByLabelText("IMAP host")).toBeInTheDocument()
  })

  it("does not render the old inline EmailImapSection block", async () => {
    render(<SourcesConnectors />, { wrapper: wrap() })
    await screen.findByText("Notes")
    // The old section had a data-testid="email-imap-section" at root level outside a dialog
    expect(screen.queryByTestId("email-imap-section")).not.toBeInTheDocument()
  })

  // ── Apple bridge rows ────────────────────────────────────────────────────

  describe("Apple bridge rows (desktop-only)", () => {
    const mockNotesScan = vi.fn()
    const mockMailScan = vi.fn()
    const mockIMessageScan = vi.fn()

    function installBridge() {
      ;(window as unknown as { cerid: object }).cerid = {
        appleConnectors: {
          notes: {
            scan: mockNotesScan,
            ingest: vi.fn().mockResolvedValue({ scan: {}, ingest: { ingested: 0, failed: 0, errors: [] } }),
          },
          mail: {
            scan: mockMailScan,
            ingest: vi.fn().mockResolvedValue({ scan: {}, ingest: { ingested: 0, failed: 0, errors: [] } }),
          },
          imessage: {
            scan: mockIMessageScan,
            ingest: vi.fn().mockResolvedValue({ scan: {}, ingested: 0, failed: 0, errors: [] }),
          },
        },
      }
    }

    beforeEach(() => {
      mockNotesScan.mockResolvedValue({
        ok: true, total_notes: 5, encrypted_skipped: 0, folder_count: 1, account_count: 1, notes: [],
      })
      mockMailScan.mockResolvedValue({
        ok: true, total_messages: 3, account_count: 1, mailbox_count: 1, scanned_with_body: 3, messages: [],
      })
      mockIMessageScan.mockResolvedValue({
        ok: true, total_conversations: 2, conversations: [],
      })
    })

    afterEach(() => {
      delete (window as unknown as { cerid?: object }).cerid
    })

    it("shows Apple Notes / Apple Mail / iMessage rows when bridge is present", async () => {
      installBridge()
      render(<SourcesConnectors />, { wrapper: wrap() })
      await screen.findByText("Notes") // folder row
      expect(await screen.findByTitle("Apple Notes")).toBeInTheDocument()
      expect(await screen.findByTitle("Apple Mail")).toBeInTheDocument()
      expect(await screen.findByTitle("iMessage")).toBeInTheDocument()
    })

    it("does NOT show Apple rows when bridge is absent (browser build)", async () => {
      // Bridge is not installed in this test
      render(<SourcesConnectors />, { wrapper: wrap() })
      await screen.findByText("Notes") // folder row still loads
      expect(screen.queryByTitle("Apple Notes")).not.toBeInTheDocument()
      expect(screen.queryByTitle("Apple Mail")).not.toBeInTheDocument()
      expect(screen.queryByTitle("iMessage")).not.toBeInTheDocument()
    })

    it("does not render the old AppleConnectorsSection inline block", async () => {
      installBridge()
      render(<SourcesConnectors />, { wrapper: wrap() })
      await screen.findByText("Notes")
      // Old section had a data-testid="apple-connectors-section"
      expect(screen.queryByTestId("apple-connectors-section")).not.toBeInTheDocument()
    })

    it("selecting Apple Notes row opens AppleDetail dialog", async () => {
      installBridge()
      render(<SourcesConnectors />, { wrapper: wrap() })
      const notesTitle = await screen.findByTitle("Apple Notes")
      fireEvent.click(notesTitle)
      // AppleDetail renders the notes scan UI (which calls scan on open)
      expect(await screen.findByText(/5 notes/)).toBeInTheDocument()
    })

    it("selecting iMessage row opens AppleDetail with conversation checklist", async () => {
      mockIMessageScan.mockResolvedValue({
        ok: true,
        total_conversations: 1,
        conversations: [
          {
            chat_id: 1, guid: "iMessage;-;+15551234567",
            display_name: null, participants: ["+15551234567"],
            message_count: 10, last_message_at: null, is_group: false,
          },
        ],
      })
      installBridge()
      render(<SourcesConnectors />, { wrapper: wrap() })
      const imsgTitle = await screen.findByTitle("iMessage")
      fireEvent.click(imsgTitle)
      expect(await screen.findByTestId("imessage-conversation-list")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Four-state matrix (Task 3.1) — loading / error / empty / success.
// "Empty" is defined as no *configured* source (see sources-connectors.tsx):
// the 7-entry connector catalog and the always-present email affordance are
// not configured on a fresh install, so rows.length alone never reaches 0.
// ---------------------------------------------------------------------------

describe("SourcesConnectors — four-state matrix", () => {
  it("loading: renders Skeleton rows, not the empty gallery", async () => {
    mockSources.mockReturnValue(new Promise(() => {}))
    mockConnectors.mockReturnValue(new Promise(() => {}))
    const { container } = render(<SourcesConnectors />, { wrapper: wrap() })

    expect(screen.getByTestId("sources-loading")).toBeInTheDocument()
    expect(screen.queryByText(/Connect your first source/i)).not.toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it("error: renders a destructive Alert with a Retry that re-invokes the queries", async () => {
    mockConnectors.mockRejectedValue(new Error("boom"))
    const { container } = render(<SourcesConnectors />, { wrapper: wrap() })

    await waitFor(() => {
      expect(screen.getByText(/Couldn't load sources/i)).toBeInTheDocument()
    })
    const retryButton = screen.getByRole("button", { name: /retry/i })
    expect(await axe(container)).toHaveNoViolations()

    mockConnectors.mockClear()
    fireEvent.click(retryButton)
    await waitFor(() => expect(mockConnectors).toHaveBeenCalled())
  })

  it("empty: renders the onboarding gallery; clicking a tile opens the add-source wizard", async () => {
    mockSources.mockResolvedValue([])
    mockConnectors.mockResolvedValue([])
    mockFetchEmailStatus.mockResolvedValue({
      configured: false, last_poll: null, messages_ingested: 0, errors: [],
    })
    mockListSourceKinds.mockResolvedValue([
      { kind: "rss", family: "feeds", tier: "core", availability: "available" },
    ])
    const onAddSource = vi.fn()
    const { container } = render(<SourcesConnectors onAddSource={onAddSource} />, { wrapper: wrap() })

    expect(await screen.findByText(/Connect your first source/i)).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()

    fireEvent.click(screen.getByRole("button", { name: /add rss/i }))
    expect(onAddSource).toHaveBeenCalledWith("rss")
  })

  it("success: renders the row list, not the loading/error/empty states", async () => {
    const { container } = render(<SourcesConnectors />, { wrapper: wrap() })

    expect(await screen.findByText("Notes")).toBeInTheDocument()
    expect(screen.queryByTestId("sources-loading")).not.toBeInTheDocument()
    expect(screen.queryByText(/Connect your first source/i)).not.toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it("empty: an oauth gallery tile opens ConnectorDetail for that connector (P0-C.4)", async () => {
    mockSources.mockResolvedValue([])
    mockFetchEmailStatus.mockResolvedValue({
      configured: false, last_poll: null, messages_ingested: 0, errors: [],
    })
    mockListSourceKinds.mockResolvedValue([
      { kind: "gmail", family: "mail", tier: "pro", availability: "oauth" },
    ])
    render(<SourcesConnectors />, { wrapper: wrap() })

    expect(await screen.findByText(/Connect your first source/i)).toBeInTheDocument()
    const tile = screen.getByRole("button", { name: /connect gmail/i })
    fireEvent.click(tile)

    // ConnectorDetail dialog opens with the status + explainer sections.
    expect(await screen.findByText("Status")).toBeInTheDocument()
    expect(screen.getByText("How this connector works")).toBeInTheDocument()
  })
})
