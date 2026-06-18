// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AppleConnectorsSection } from "@/components/sources/apple-connectors-section"

const mockNotesScan = vi.fn()
const mockNotesIngest = vi.fn()
const mockMailScan = vi.fn()
const mockMailIngest = vi.fn()
const mockRemindersScan = vi.fn()
const mockRemindersIngest = vi.fn()
const mockIMessageScan = vi.fn()
const mockIMessageIngest = vi.fn()

// Backwards-compat aliases (original tests referenced mockScan/mockIngest = notes)
const mockScan = mockNotesScan
const mockIngest = mockNotesIngest

beforeEach(() => {
  mockNotesScan.mockReset()
  mockNotesIngest.mockReset()
  mockMailScan.mockReset()
  mockMailIngest.mockReset()
  mockRemindersScan.mockReset()
  mockRemindersIngest.mockReset()
  mockIMessageScan.mockReset()
  mockIMessageIngest.mockReset()
  // Default: all connectors return clean empty scans so existing notes tests
  // don't have to set up the mail + imessage mocks.
  mockMailScan.mockResolvedValue({
    ok: true,
    total_messages: 0,
    account_count: 0,
    mailbox_count: 0,
    scanned_with_body: 0,
    messages: [],
  })
  mockRemindersScan.mockResolvedValue({
    ok: true,
    total_reminders: 0,
    list_count: 0,
    reminders: [],
  })
  mockIMessageScan.mockResolvedValue({
    ok: true,
    total_conversations: 0,
    conversations: [],
  })
  ;(window as unknown as { cerid: object }).cerid = {
    appleConnectors: {
      notes: { scan: mockNotesScan, ingest: mockNotesIngest },
      mail: { scan: mockMailScan, ingest: mockMailIngest },
      reminders: { scan: mockRemindersScan, ingest: mockRemindersIngest },
      imessage: { scan: mockIMessageScan, ingest: mockIMessageIngest },
    },
  }
})

afterEach(() => {
  delete (window as unknown as { cerid?: object }).cerid
})

describe("AppleConnectorsSection", () => {
  it("renders nothing when desktop bridge unavailable", () => {
    delete (window as unknown as { cerid?: object }).cerid
    const { container } = render(<AppleConnectorsSection />)
    expect(container.innerHTML).toBe("")
  })

  it("scans on mount and shows note + encrypted counts", async () => {
    mockScan.mockResolvedValue({
      ok: true,
      total_notes: 42,
      encrypted_skipped: 3,
      folder_count: 5,
      account_count: 2,
      notes: [],
    })
    render(<AppleConnectorsSection />)
    expect(await screen.findByText(/42 notes/)).toBeInTheDocument()
    expect(screen.getByText(/5 folders/)).toBeInTheDocument()
    expect(screen.getByText(/2 accounts/)).toBeInTheDocument()
    expect(screen.getByText(/3 encrypted/)).toBeInTheDocument()
  })

  it("shows FDA error when scan returns ok=false", async () => {
    mockScan.mockResolvedValue({
      ok: false,
      total_notes: 0,
      encrypted_skipped: 0,
      folder_count: 0,
      account_count: 0,
      notes: [],
      error: "Full Disk Access required to read Apple Notes…",
    })
    render(<AppleConnectorsSection />)
    expect(await screen.findByText(/needs access/i)).toBeInTheDocument()
    const matches = await screen.findAllByText(/Full Disk Access/)
    expect(matches.length).toBeGreaterThanOrEqual(1)
  })

  it("ingest button POSTs and surfaces result", async () => {
    mockScan.mockResolvedValue({
      ok: true,
      total_notes: 10,
      encrypted_skipped: 0,
      folder_count: 1,
      account_count: 1,
      notes: [],
    })
    mockIngest.mockResolvedValue({
      scan: { ok: true, total_notes: 10, encrypted_skipped: 0, folder_count: 1, account_count: 1 },
      ingest: { ingested: 10, failed: 0, errors: [] },
    })
    const user = userEvent.setup()
    render(<AppleConnectorsSection />)
    await user.click(await screen.findByTestId("apple-notes-ingest"))
    await waitFor(() => {
      expect(mockIngest).toHaveBeenCalled()
    })
    expect(await screen.findByTestId("apple-notes-ingest-result")).toHaveTextContent(/10 ingested/)
  })

  it("ingest button disabled when no notes", async () => {
    mockScan.mockResolvedValue({
      ok: true,
      total_notes: 0,
      encrypted_skipped: 0,
      folder_count: 0,
      account_count: 0,
      notes: [],
    })
    render(<AppleConnectorsSection />)
    const btn = await screen.findByTestId("apple-notes-ingest")
    expect(btn).toBeDisabled()
  })

  it("refresh button retriggers scan", async () => {
    mockScan.mockResolvedValue({
      ok: true,
      total_notes: 5,
      encrypted_skipped: 0,
      folder_count: 0,
      account_count: 0,
      notes: [],
    })
    const user = userEvent.setup()
    render(<AppleConnectorsSection />)
    await screen.findByText(/5 notes/)
    expect(mockScan).toHaveBeenCalledTimes(1)
    await user.click(screen.getByTestId("apple-connectors-refresh"))
    await waitFor(() => {
      expect(mockScan).toHaveBeenCalledTimes(2)
    })
  })

  it("'1 note' uses singular (no plural s)", async () => {
    mockScan.mockResolvedValue({
      ok: true,
      total_notes: 1,
      encrypted_skipped: 0,
      folder_count: 0,
      account_count: 0,
      notes: [],
    })
    render(<AppleConnectorsSection />)
    const row = await screen.findByTestId("apple-notes-row")
    expect(row.textContent).toMatch(/1 note(?!s)/)
  })

  // ── Mail ──────────────────────────────────────────────────────────
  it("Mail row renders counts when scan succeeds", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockMailScan.mockResolvedValue({
      ok: true,
      total_messages: 250,
      account_count: 2,
      mailbox_count: 8,
      scanned_with_body: 245,
      messages: [],
    })
    render(<AppleConnectorsSection />)
    const row = await screen.findByTestId("apple-mail-row")
    expect(row.textContent).toMatch(/250 messages/)
    expect(row.textContent).toMatch(/2 accounts/)
    expect(row.textContent).toMatch(/8 mailboxes/)
    expect(row.textContent).toMatch(/5 body unreadable/)
  })

  it("Mail ingest button POSTs and surfaces result", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockMailScan.mockResolvedValue({
      ok: true, total_messages: 10, account_count: 1, mailbox_count: 1, scanned_with_body: 10, messages: [],
    })
    mockMailIngest.mockResolvedValue({
      scan: { ok: true, total_messages: 10, account_count: 1, mailbox_count: 1, scanned_with_body: 10 },
      ingest: { ingested: 10, failed: 0, errors: [] },
    })
    const user = userEvent.setup()
    render(<AppleConnectorsSection />)
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    await waitFor(() => {
      expect(mockMailIngest).toHaveBeenCalled()
    })
    expect(await screen.findByTestId("apple-mail-ingest-result")).toHaveTextContent(/10 ingested/)
  })

  // ── iMessage ─────────────────────────────────────────────────────
  it("iMessage row lists conversations with opt-in checkboxes", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockIMessageScan.mockResolvedValue({
      ok: true,
      total_conversations: 2,
      conversations: [
        {
          chat_id: 1,
          guid: "iMessage;-;+15551234567",
          display_name: null,
          participants: ["+15551234567"],
          message_count: 42,
          last_message_at: "2026-05-01T00:00:00Z",
          is_group: false,
        },
        {
          chat_id: 2,
          guid: "iMessage;+;chat-uuid-2",
          display_name: "Team Group",
          participants: ["alice@example.com", "bob@example.com"],
          message_count: 99,
          last_message_at: "2026-05-21T00:00:00Z",
          is_group: true,
        },
      ],
    })
    render(<AppleConnectorsSection />)
    const list = await screen.findByTestId("imessage-conversation-list")
    expect(list.textContent).toMatch(/\+15551234567/)
    expect(list.textContent).toMatch(/Team Group/)
    expect(list.textContent).toMatch(/group/)
  })

  it("iMessage ingest button disabled when no chats selected", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockIMessageScan.mockResolvedValue({
      ok: true,
      total_conversations: 1,
      conversations: [
        {
          chat_id: 1, guid: "iMessage;-;+15551234567",
          display_name: null, participants: ["+15551234567"],
          message_count: 42, last_message_at: null, is_group: false,
        },
      ],
    })
    render(<AppleConnectorsSection />)
    const btn = await screen.findByTestId("imessage-ingest")
    expect(btn).toBeDisabled()
  })

  it("iMessage ingest fires with selected chat guids", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockIMessageScan.mockResolvedValue({
      ok: true,
      total_conversations: 1,
      conversations: [
        {
          chat_id: 1, guid: "iMessage;-;test-guid",
          display_name: null, participants: ["+15551234567"],
          message_count: 5, last_message_at: null, is_group: false,
        },
      ],
    })
    mockIMessageIngest.mockResolvedValue({
      scan: { ok: true, total_conversations: 1 },
      ingested: 1,
      failed: 0,
      errors: [],
    })
    const user = userEvent.setup()
    render(<AppleConnectorsSection />)
    await user.click(await screen.findByTestId("imessage-chat-iMessage;-;test-guid"))
    await user.click(screen.getByTestId("imessage-ingest"))
    await waitFor(() => {
      expect(mockIMessageIngest).toHaveBeenCalledWith({
        mcp_base_url: expect.any(String),
        chat_guids: ["iMessage;-;test-guid"],
        limit_per_chat: 5000,
      })
    })
    expect(await screen.findByTestId("imessage-ingest-result")).toHaveTextContent(/1 conversation ingested/)
  })

  it("Reminders row renders counts when scan succeeds", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockRemindersScan.mockResolvedValue({
      ok: true,
      total_reminders: 37,
      list_count: 4,
      reminders: [],
    })
    render(<AppleConnectorsSection />)
    const row = await screen.findByTestId("apple-reminders-row")
    expect(row.textContent).toMatch(/37 reminders/)
    expect(row.textContent).toMatch(/4 lists/)
  })

  it("Reminders row shows needs-access when the helper is denied", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockRemindersScan.mockResolvedValue({
      ok: false,
      total_reminders: 0,
      list_count: 0,
      error: "Reminders TCC access not granted",
      reminders: [],
    })
    render(<AppleConnectorsSection />)
    const row = await screen.findByTestId("apple-reminders-row")
    expect(row.textContent).toMatch(/needs access/)
    expect(row.textContent).toMatch(/TCC access not granted/)
  })

  it("Reminders ingest button POSTs and surfaces result", async () => {
    mockNotesScan.mockResolvedValue({
      ok: true, total_notes: 0, encrypted_skipped: 0, folder_count: 0, account_count: 0, notes: [],
    })
    mockRemindersScan.mockResolvedValue({
      ok: true, total_reminders: 12, list_count: 2, reminders: [],
    })
    mockRemindersIngest.mockResolvedValue({
      scan: { ok: true, total_reminders: 12, list_count: 2 },
      ingest: { ingested: 12, failed: 0, errors: [] },
    })
    const user = userEvent.setup()
    render(<AppleConnectorsSection />)
    await user.click(await screen.findByTestId("apple-reminders-ingest"))
    await waitFor(() => {
      expect(mockRemindersIngest).toHaveBeenCalledWith({ mcp_base_url: expect.any(String) })
    })
    expect(await screen.findByTestId("apple-reminders-ingest-result")).toHaveTextContent(
      /12 ingested/,
    )
  })
})
