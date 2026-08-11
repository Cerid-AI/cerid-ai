// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"

// AppleDetail resolves its own Pro entitlement now (the bridge kinds have no
// backend chokepoint), so capabilities must be mocked here — otherwise
// fetchCapabilities hits real fetch in jsdom, rejects, and every test below
// runs against the community-tier default and renders the upgrade pane.
vi.mock("@/lib/api/billing", () => ({
  fetchCapabilities: vi.fn(),
}))

import { fetchCapabilities } from "@/lib/api/billing"
const mockCapabilities = fetchCapabilities as ReturnType<typeof vi.fn>

/** Realistic capabilities payload: `features` carries the per-flag detail the
    hook actually resolves, not the empty map that silently forces every test
    down the registry-tier fallback. */
function capabilities(
  tier: "community" | "pro" | "enterprise",
  features: Record<string, { enabled: boolean; tier_required: string }>,
) {
  return { tier, features, buckets: {} }
}

const APPLE_FLAGS = ["apple_notes_reader", "apple_mail_reader", "imessage_reader"]

/** Every Apple bridge flag on, at Pro tier. */
function allEntitled() {
  return capabilities(
    "pro",
    Object.fromEntries(
      APPLE_FLAGS.map((f) => [f, { enabled: true, tier_required: "pro" }]),
    ),
  )
}

// Mock the bridge fns
const mockNotesScan = vi.fn()
const mockNotesIngest = vi.fn()
const mockMailScan = vi.fn()
const mockMailIngest = vi.fn()
const mockIMessageScan = vi.fn()
const mockIMessageIngest = vi.fn()

function installBridge() {
  ;(window as unknown as { cerid: object }).cerid = {
    appleConnectors: {
      notes: { scan: mockNotesScan, ingest: mockNotesIngest },
      mail: { scan: mockMailScan, ingest: mockMailIngest },
      imessage: { scan: mockIMessageScan, ingest: mockIMessageIngest },
    },
  }
}

function removeBridge() {
  delete (window as unknown as { cerid?: object }).cerid
}

/** AppleDetail reads capabilities through react-query, so every render needs a
    client. A fresh one per render keeps the entitlement cache from leaking
    between the entitled and locked cases. */
function renderDetail(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(ui, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  // Default to an entitled server so the scan/ingest tests exercise the real
  // bridge flow rather than the upgrade pane.
  mockCapabilities.mockResolvedValue(allEntitled())
  // Default: minimal successful responses so tests that don't care about detail
  // don't have to set up every mock.
  mockNotesScan.mockResolvedValue({
    ok: true, total_notes: 5, encrypted_skipped: 0, folder_count: 1, account_count: 1, notes: [],
  })
  mockNotesIngest.mockResolvedValue({
    scan: { ok: true, total_notes: 5, encrypted_skipped: 0, folder_count: 1, account_count: 1 },
    ingest: { ingested: 5, failed: 0, errors: [] },
  })
  mockMailScan.mockResolvedValue({
    ok: true, total_messages: 10, account_count: 1, mailbox_count: 2, scanned_with_body: 10, messages: [],
  })
  mockMailIngest.mockResolvedValue({
    scan: { ok: true, total_messages: 10, account_count: 1, mailbox_count: 2, scanned_with_body: 10 },
    ingest: { ingested: 10, failed: 0, errors: [] },
  })
  mockIMessageScan.mockResolvedValue({
    ok: true, total_conversations: 2, conversations: [
      {
        chat_id: 1, guid: "iMessage;-;+15551234567",
        display_name: null, participants: ["+15551234567"],
        message_count: 42, last_message_at: null, is_group: false,
      },
      {
        chat_id: 2, guid: "iMessage;+;chat-uuid-2",
        display_name: "Team Group", participants: ["alice@example.com", "bob@example.com"],
        message_count: 99, last_message_at: null, is_group: true,
      },
    ],
  })
  mockIMessageIngest.mockResolvedValue({
    scan: { ok: true, total_conversations: 2 },
    ingested: 1, failed: 0, errors: [],
  })
  installBridge()
})

afterEach(() => {
  removeBridge()
})

// We import AppleDetail after beforeEach sets up window.cerid
import { AppleDetail } from "@/components/sources/apple-detail"

// ---------------------------------------------------------------------------
// Notes
// ---------------------------------------------------------------------------

describe("AppleDetail — notes", () => {
  it("shows desktop-only message when bridge is absent", async () => {
    removeBridge()
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    // Dialog opens but shows the desktop-only fallback message
    expect(await screen.findByText(/desktop/i)).toBeInTheDocument()
    // No scan/ingest UI rendered
    expect(screen.queryByTestId("apple-notes-ingest")).not.toBeInTheDocument()
  })

  it("scans on open and shows note counts", async () => {
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    expect(await screen.findByText(/5 notes/)).toBeInTheDocument()
    expect(screen.getByText(/1 folder/)).toBeInTheDocument()
    expect(screen.getByText(/1 account/)).toBeInTheDocument()
  })

  it("ingest button calls notes.ingest and shows result", async () => {
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-notes-ingest"))
    await waitFor(() => expect(mockNotesIngest).toHaveBeenCalled())
    expect(await screen.findByTestId("apple-notes-ingest-result")).toHaveTextContent(/5 ingested/)
  })

  it("is axe-clean (notes)", async () => {
    const { container } = renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    await screen.findByText(/5 notes/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Mail
// ---------------------------------------------------------------------------

describe("AppleDetail — mail", () => {
  it("scans on open and shows mail counts", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    expect(await screen.findByText(/10 messages/)).toBeInTheDocument()
    expect(screen.getByText(/1 account/)).toBeInTheDocument()
    expect(screen.getByText(/2 mailboxes/)).toBeInTheDocument()
  })

  it("ingest button calls mail.ingest and shows result", async () => {
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    await waitFor(() => expect(mockMailIngest).toHaveBeenCalled())
    expect(await screen.findByTestId("apple-mail-ingest-result")).toHaveTextContent(/10 ingested/)
  })

  it("is axe-clean (mail)", async () => {
    const { container } = renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// iMessage — privacy-first opt-in flow
// ---------------------------------------------------------------------------

describe("AppleDetail — imessage", () => {
  it("renders conversation checklist from scan result", async () => {
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    const list = await screen.findByTestId("imessage-conversation-list")
    expect(list.textContent).toMatch(/\+15551234567/)
    expect(list.textContent).toMatch(/Team Group/)
    expect(list.textContent).toMatch(/group/)
  })

  it("ingest button is disabled until at least one chat is selected", async () => {
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    const btn = await screen.findByTestId("imessage-ingest")
    expect(btn).toBeDisabled()
  })

  it("ingest fires with selected chat_guids and limit_per_chat=5000", async () => {
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    // Select first conversation
    await user.click(await screen.findByTestId("imessage-chat-iMessage;-;+15551234567"))
    await user.click(screen.getByTestId("imessage-ingest"))
    await waitFor(() =>
      expect(mockIMessageIngest).toHaveBeenCalledWith({
        mcp_base_url: expect.any(String),
        chat_guids: ["iMessage;-;+15551234567"],
        limit_per_chat: 5000,
      }),
    )
    expect(await screen.findByTestId("imessage-ingest-result")).toHaveTextContent(/1 conversation ingested/)
  })

  it("selecting multiple chats includes all guids", async () => {
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("imessage-chat-iMessage;-;+15551234567"))
    await user.click(screen.getByTestId("imessage-chat-iMessage;+;chat-uuid-2"))
    // Button label shows count
    expect(screen.getByTestId("imessage-ingest").textContent).toMatch(/2/)
  })

  it("is axe-clean (imessage, no nested-interactive violations)", async () => {
    const { container } = renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    await screen.findByTestId("imessage-conversation-list")
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Pro gate
//
// The three bridge kinds are Pro with NO backend chokepoint — they never reach
// the plugin loader and ingest through the generic /ingest/structured route —
// so this renderer check is the only thing between a community desktop user and
// their Mail archive. `appleRows()` takes a required isLocked predicate for the
// row path; these cases assert the same promise holds when the pane is mounted
// directly, which is how it was previously bypassed.
// ---------------------------------------------------------------------------

describe("AppleDetail — Pro gate", () => {
  it("shows the upgrade path instead of the pane when the tier is too low", async () => {
    mockCapabilities.mockResolvedValue(
      capabilities("community", {
        apple_mail_reader: { enabled: false, tier_required: "pro" },
      }),
    )
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)

    expect(await screen.findByText(/Apple Mail is part of Cerid Pro/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /unlock with pro/i })).toBeInTheDocument()
    // The pane is the scan/ingest surface — rendering it hands over the feature.
    expect(screen.queryByTestId("apple-mail-ingest")).not.toBeInTheDocument()
    await waitFor(() => expect(mockMailScan).not.toHaveBeenCalled())
  })

  it("clicking Unlock with Pro opens the upgrade overlay", async () => {
    const user = userEvent.setup()
    mockCapabilities.mockResolvedValue(
      capabilities("community", {
        apple_notes_reader: { enabled: false, tier_required: "pro" },
      }),
    )
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)

    await user.click(await screen.findByRole("button", { name: /unlock with pro/i }))
    expect(await screen.findByText(/requires Cerid Pro/i)).toBeInTheDocument()
  })

  it("locks per flag, not per tier: a Pro tier still can't open an Enterprise kind", async () => {
    // Detail-driven branch: `tier_required` comes off the feature entry, so the
    // verdict differs per kind on one and the same server. A fixture with
    // `features: {}` could never tell these two apart.
    mockCapabilities.mockResolvedValue(
      capabilities("pro", {
        apple_notes_reader: { enabled: true, tier_required: "pro" },
        imessage_reader: { enabled: false, tier_required: "enterprise" },
      }),
    )
    const { unmount } = renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    expect(await screen.findByText(/iMessage is part of Cerid Pro/i)).toBeInTheDocument()
    unmount()

    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    expect(await screen.findByText(/5 notes/)).toBeInTheDocument()
  })

  it("does not pitch an upgrade to a Pro user whose server flag is off", async () => {
    // "flag-off" is not "locked": the plan already covers it, the operator
    // turned it off. Selling Pro to a Pro customer is the wrong answer.
    mockCapabilities.mockResolvedValue(
      capabilities("pro", {
        apple_mail_reader: { enabled: false, tier_required: "pro" },
      }),
    )
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    expect(await screen.findByText(/10 messages/)).toBeInTheDocument()
    expect(screen.queryByText(/part of Cerid Pro/i)).not.toBeInTheDocument()
  })

  it("fails CLOSED when capabilities cannot be loaded", async () => {
    // Without the registry-tier fallback argument, an unresolvable flag returns
    // AVAILABLE — the pane would open exactly when the server can't be asked.
    mockCapabilities.mockRejectedValue(new Error("network down"))
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    expect(await screen.findByText(/Apple Notes is part of Cerid Pro/i)).toBeInTheDocument()
  })
})
