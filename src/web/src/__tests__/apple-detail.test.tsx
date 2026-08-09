// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"

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

beforeEach(() => {
  vi.clearAllMocks()
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
    render(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    // Dialog opens but shows the desktop-only fallback message
    expect(await screen.findByText(/desktop/i)).toBeInTheDocument()
    // No scan/ingest UI rendered
    expect(screen.queryByTestId("apple-notes-ingest")).not.toBeInTheDocument()
  })

  it("scans on open and shows note counts", async () => {
    render(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    expect(await screen.findByText(/5 notes/)).toBeInTheDocument()
    expect(screen.getByText(/1 folder/)).toBeInTheDocument()
    expect(screen.getByText(/1 account/)).toBeInTheDocument()
  })

  it("ingest button calls notes.ingest and shows result", async () => {
    const user = userEvent.setup()
    render(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-notes-ingest"))
    await waitFor(() => expect(mockNotesIngest).toHaveBeenCalled())
    expect(await screen.findByTestId("apple-notes-ingest-result")).toHaveTextContent(/5 ingested/)
  })

  it("is axe-clean (notes)", async () => {
    const { container } = render(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    await screen.findByText(/5 notes/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Mail
// ---------------------------------------------------------------------------

describe("AppleDetail — mail", () => {
  it("scans on open and shows mail counts", async () => {
    render(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    expect(await screen.findByText(/10 messages/)).toBeInTheDocument()
    expect(screen.getByText(/1 account/)).toBeInTheDocument()
    expect(screen.getByText(/2 mailboxes/)).toBeInTheDocument()
  })

  it("ingest button calls mail.ingest and shows result", async () => {
    const user = userEvent.setup()
    render(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    await waitFor(() => expect(mockMailIngest).toHaveBeenCalled())
    expect(await screen.findByTestId("apple-mail-ingest-result")).toHaveTextContent(/10 ingested/)
  })

  it("is axe-clean (mail)", async () => {
    const { container } = render(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// iMessage — privacy-first opt-in flow
// ---------------------------------------------------------------------------

describe("AppleDetail — imessage", () => {
  it("renders conversation checklist from scan result", async () => {
    render(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    const list = await screen.findByTestId("imessage-conversation-list")
    expect(list.textContent).toMatch(/\+15551234567/)
    expect(list.textContent).toMatch(/Team Group/)
    expect(list.textContent).toMatch(/group/)
  })

  it("ingest button is disabled until at least one chat is selected", async () => {
    render(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    const btn = await screen.findByTestId("imessage-ingest")
    expect(btn).toBeDisabled()
  })

  it("ingest fires with selected chat_guids and limit_per_chat=5000", async () => {
    const user = userEvent.setup()
    render(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
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
    render(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("imessage-chat-iMessage;-;+15551234567"))
    await user.click(screen.getByTestId("imessage-chat-iMessage;+;chat-uuid-2"))
    // Button label shows count
    expect(screen.getByTestId("imessage-ingest").textContent).toMatch(/2/)
  })

  it("is axe-clean (imessage, no nested-interactive violations)", async () => {
    const { container } = render(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    await screen.findByTestId("imessage-conversation-list")
    expect(await axe(container)).toHaveNoViolations()
  })
})
