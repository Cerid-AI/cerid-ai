// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, render, screen, waitFor } from "@testing-library/react"
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

const APPLE_FLAGS = [
  "apple_notes_reader",
  "apple_mail_reader",
  "imessage_reader",
  "apple_calendar_eventkit",
  "apple_photos_reader",
  "reminders_eventkit",
]

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
const mockCalendarScan = vi.fn()
const mockCalendarIngest = vi.fn()
const mockPhotosScan = vi.fn()
const mockPhotosIngest = vi.fn()

function installBridge() {
  ;(window as unknown as { cerid: object }).cerid = {
    appleConnectors: {
      notes: { scan: mockNotesScan, ingest: mockNotesIngest },
      mail: { scan: mockMailScan, ingest: mockMailIngest },
      imessage: { scan: mockIMessageScan, ingest: mockIMessageIngest },
      calendar: { scan: mockCalendarScan, ingest: mockCalendarIngest },
      photos: { scan: mockPhotosScan, ingest: mockPhotosIngest },
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

/** Like renderDetail, but with a real NavigationProvider so cross-pane
    affordances (Fix permission) can be asserted against the pane change and
    the URL params they write, not just against onClose. */
function renderDetailWithNav(ui: React.ReactElement, onPaneChange: (pane: string) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(ui, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>
        <NavigationProvider activePane="sources" onPaneChange={onPaneChange as never}>
          {children}
        </NavigationProvider>
      </QueryClientProvider>
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
    ingest: { ingested: 10, failed: 0, skipped: 0, errors: [] },
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
    ingested: 1, failed: 0, skipped_no_text: 0, notes: [], errors: [],
  })
  mockCalendarScan.mockResolvedValue({
    ok: true, total_events: 4, calendar_count: 2, events: [],
  })
  mockCalendarIngest.mockResolvedValue({
    scan: { ok: true, total_events: 4, calendar_count: 2 },
    ingest: { ingested: 4, failed: 0, errors: [] },
  })
  mockPhotosScan.mockResolvedValue({
    ok: true, total_photos: 6, photos: [],
  })
  mockPhotosIngest.mockResolvedValue({
    scan: { ok: true, total_photos: 6 },
    ingest: { ingested: 6, failed: 0, errors: [] },
  })
  installBridge()
})

afterEach(() => {
  removeBridge()
})

// We import AppleDetail after beforeEach sets up window.cerid
import { AppleDetail } from "@/components/sources/apple-detail"
import { NavigationProvider } from "@/contexts/navigation-context"

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

  it("ingest button calls notes.ingest with the SAME limit the scan used", async () => {
    // Scan-vs-ingest limit drift (WB-53): omitting the limit fell through to
    // the main process's 5000 default, ingesting a different population than
    // the one the scan showed and the user consented to.
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    await waitFor(() => expect(mockNotesScan).toHaveBeenCalledWith({ limit: 100 }))
    await user.click(await screen.findByTestId("apple-notes-ingest"))
    await waitFor(() =>
      expect(mockNotesIngest).toHaveBeenCalledWith({
        mcp_base_url: expect.any(String),
        limit: 100,
      }),
    )
    expect(await screen.findByTestId("apple-notes-ingest-result")).toHaveTextContent(/5 ingested/)
    // A successful ingest closes the loop: the result line links to the
    // live ingestion stream in Sources → Activity.
    expect(screen.getByTestId("view-in-activity")).toBeInTheDocument()
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

  it("footer disclaimer talks about Mail, not Notes/iMessage (UX-25)", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    // The shared footer used to show the Notes/iMessage disclaimer on
    // every kind — misleading on Mail.
    expect(screen.queryByText(/Encrypted notes/)).not.toBeInTheDocument()
    expect(screen.queryByText(/opt-in per conversation/)).not.toBeInTheDocument()
    expect(screen.getByText(/Mail is read from the local Mail\.app archive/)).toBeInTheDocument()
  })

  it("ingest button calls mail.ingest with the SAME limit the scan used", async () => {
    // See the notes twin: mail's main-process default was 500 vs the scan's 200.
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await waitFor(() => expect(mockMailScan).toHaveBeenCalledWith({ limit: 200 }))
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    await waitFor(() =>
      expect(mockMailIngest).toHaveBeenCalledWith({
        mcp_base_url: expect.any(String),
        limit: 200,
      }),
    )
    expect(await screen.findByTestId("apple-mail-ingest-result")).toHaveTextContent(/10 ingested/)
  })

  it("presents a scan that hit the cap as a preview, not a census (WB-33 / spec item 10)", async () => {
    mockMailScan.mockResolvedValue({
      ok: true, total_messages: 200, account_count: 1, mailbox_count: 2, scanned_with_body: 200, messages: [],
    })
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    expect(await screen.findByText(/first 200 messages \(scan preview\)/)).toBeInTheDocument()
    expect(screen.queryByText(/^200 messages/)).not.toBeInTheDocument()
  })

  it("surfaces skipped body-less messages in the sync result, not just the scan line", async () => {
    // The failure mode this guards: every message body-less → ingest loop
    // skips all input and the result used to render as a clean "0 ingested".
    mockMailIngest.mockResolvedValue({
      scan: { ok: true, total_messages: 10, account_count: 1, mailbox_count: 2, scanned_with_body: 0 },
      ingest: { ingested: 0, failed: 0, skipped: 10, errors: [] },
    })
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    const result = await screen.findByTestId("apple-mail-ingest-result")
    expect(result).toHaveTextContent(/0 ingested/)
    expect(result).toHaveTextContent(/10 skipped \(body unreadable\)/)
  })

  it("is axe-clean (mail)", async () => {
    const { container } = renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("'needs access' offers Fix permission, which closes the dialog and lands on Settings → System permissions (spec item 4)", async () => {
    mockMailScan.mockResolvedValue({
      ok: false, total_messages: 0, account_count: 0, mailbox_count: 0,
      scanned_with_body: 0, error: "Full Disk Access required", messages: [],
    })
    const onClose = vi.fn()
    const onPaneChange = vi.fn()
    const user = userEvent.setup()
    renderDetailWithNav(<AppleDetail kind="mail" open onClose={onClose} />, onPaneChange)

    expect(await screen.findByText(/needs access/)).toBeInTheDocument()
    await user.click(screen.getByTestId("fix-permission"))

    expect(onClose).toHaveBeenCalled()
    expect(onPaneChange).toHaveBeenCalledWith("settings")
    const params = new URLSearchParams(window.location.search)
    expect(params.get("category")).toBe("system")
    expect(params.get("setting")).toBe("system.permissions")
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

  it("surfaces dropped messages and per-conversation shortfalls (WB-49/WB-52)", async () => {
    // 12 rows had no readable text and one thread was capped: both facts must
    // land in the UI — a clean "1 conversation ingested" for a partial sync is
    // the silent-drop the counters exist to prevent.
    mockIMessageIngest.mockResolvedValue({
      scan: { ok: true, total_conversations: 2 },
      ingested: 1,
      failed: 0,
      skipped_no_text: 12,
      notes: ["+15551234567: ingested 5000 of 8010 messages (newest kept)"],
      errors: [],
    })
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("imessage-chat-iMessage;-;+15551234567"))
    await user.click(screen.getByTestId("imessage-ingest"))
    const result = await screen.findByTestId("imessage-ingest-result")
    expect(result).toHaveTextContent(/12 messages skipped \(no readable text\)/)
    expect(screen.getByTestId("imessage-ingest-note")).toHaveTextContent(
      /ingested 5000 of 8010 messages/,
    )
  })

  it("caps the checklist at 50 with a visible caption and end-of-list marker (WB-30)", async () => {
    const conversations = Array.from({ length: 60 }, (_, i) => ({
      chat_id: i, guid: `guid-${i}`, display_name: `Chat ${i}`,
      participants: [], message_count: 1, last_message_at: null, is_group: false,
    }))
    mockIMessageScan.mockResolvedValue({ ok: true, total_conversations: 60, conversations })
    renderDetail(<AppleDetail kind="imessage" open onClose={vi.fn()} />)

    const list = await screen.findByTestId("imessage-conversation-list")
    // 50 conversation rows + 1 end-of-list marker; the cut is stated, not silent.
    expect(list.querySelectorAll("li")).toHaveLength(51)
    expect(screen.getByText(/showing 50 of 60/)).toBeInTheDocument()
    expect(screen.getByTestId("imessage-list-end")).toHaveTextContent(/10 more scanned conversations not shown/)
  })
})

// ---------------------------------------------------------------------------
// Calendar / Photos
// ---------------------------------------------------------------------------

describe("AppleDetail — calendar and photos", () => {
  it("renders the collected ingest errors, not just a failed-count (WB-60)", async () => {
    // The errors array was collected all along and never displayed — "2
    // failed" with no reason is what let a 100% metadata-shape failure read
    // as a flaky network.
    mockCalendarIngest.mockResolvedValue({
      scan: { ok: true, total_events: 4, calendar_count: 2 },
      ingest: {
        ingested: 2,
        failed: 2,
        errors: ["HTTP 422 for x-apple:1", "HTTP 422 for x-apple:2"],
      },
    })
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="calendar" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-ingest"))
    await screen.findByTestId("ingest-summary")
    const errors = screen.getAllByTestId("ingest-error")
    expect(errors).toHaveLength(2)
    expect(errors[0]).toHaveTextContent("HTTP 422 for x-apple:1")
  })

  it("surfaces a limited Photos grant on the pane, not only in the wizard (WB-62)", async () => {
    mockPhotosScan.mockResolvedValue({ ok: true, total_photos: 6, limited: true, photos: [] })
    renderDetail(<AppleDetail kind="photos" open onClose={vi.fn()} />)
    expect(await screen.findByTestId("photos-limited")).toHaveTextContent(/Limited access/)
  })

  it("does not render the limited note on a full-library grant", async () => {
    mockPhotosScan.mockResolvedValue({ ok: true, total_photos: 6, limited: false, photos: [] })
    renderDetail(<AppleDetail kind="photos" open onClose={vi.fn()} />)
    await screen.findByTestId("photos-summary")
    expect(screen.queryByTestId("photos-limited")).not.toBeInTheDocument()
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
    // WB-09: it must also say WHY — "is part of Cerid Pro" is a wrong, confident
    // claim about a paying customer's tier when the truth is "couldn't check".
    mockCapabilities.mockRejectedValue(new Error("network down"))
    renderDetail(<AppleDetail kind="notes" open onClose={vi.fn()} />)
    expect(await screen.findByText(/Couldn.t confirm your plan/i)).toBeInTheDocument()
    expect(screen.queryByText(/Apple Notes is part of Cerid Pro/i)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Background sync (sf-1 status truth) — when the desktop bridge exposes the
// resumable sync API, "Sync to KB" starts a main-process background sync and
// the card renders live N/M + rate + ETA from progress events instead of a
// bare spinner that contradicts a green "ready" chip (UX-23/24).
// ---------------------------------------------------------------------------

describe("AppleDetail — background sync", () => {
  const mockSyncStart = vi.fn()
  const mockSyncStatus = vi.fn()
  let progressCb: ((p: Record<string, unknown>) => void) | null = null

  function installSyncBridge() {
    ;(window as unknown as { cerid: object }).cerid = {
      appleConnectors: {
        notes: { scan: mockNotesScan, ingest: mockNotesIngest },
        mail: { scan: mockMailScan, ingest: mockMailIngest },
        imessage: { scan: mockIMessageScan, ingest: mockIMessageIngest },
        sync: {
          start: mockSyncStart,
          status: mockSyncStatus,
          pause: vi.fn(),
          resume: vi.fn(),
          onProgress: (cb: (p: Record<string, unknown>) => void) => {
            progressCb = cb
            return () => {
              progressCb = null
            }
          },
        },
      },
    }
  }

  function progress(overrides: Record<string, unknown> = {}) {
    return {
      kind: "apple_mail",
      state: "syncing",
      total: 200,
      posted: 120,
      failed: 2,
      skippedFromCursor: 0,
      ratePerMin: 12.5,
      etaSeconds: 384,
      startedAt: new Date().toISOString(),
      lastError: null,
      ...overrides,
    }
  }

  beforeEach(() => {
    mockSyncStart.mockResolvedValue({ started: true })
    mockSyncStatus.mockResolvedValue([])
    installSyncBridge()
  })

  it("Sync to KB starts the background sync instead of the awaited ingest", async () => {
    const user = userEvent.setup()
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await user.click(await screen.findByTestId("apple-mail-ingest"))
    await waitFor(() =>
      expect(mockSyncStart).toHaveBeenCalledWith({
        kind: "mail",
        mcp_base_url: expect.any(String),
      }),
    )
    expect(mockMailIngest).not.toHaveBeenCalled()
  })

  it("renders live N/M, rate and ETA from progress events", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    act(() => progressCb?.(progress()))
    const line = await screen.findByTestId("apple-mail-sync-progress")
    expect(line.textContent).toMatch(/120\/200 synced/)
    expect(line.textContent).toMatch(/12\.5\/min/)
    expect(line.textContent).toMatch(/2 failed/)
    expect(line.textContent).toMatch(/background/)
  })

  it("replaces the green ready chip with a syncing chip while active", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    expect(screen.getByText(/ready/)).toBeInTheDocument()
    act(() => progressCb?.(progress()))
    await waitFor(() => expect(screen.queryByText(/ready/)).not.toBeInTheDocument())
    expect(screen.getByText(/^syncing$/)).toBeInTheDocument()
    // the sync button is disabled while a sync runs
    expect(screen.getByTestId("apple-mail-ingest")).toBeDisabled()
  })

  it("ignores progress for other kinds", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    act(() => progressCb?.(progress({ kind: "apple_notes" })))
    expect(screen.queryByTestId("apple-mail-sync-progress")).not.toBeInTheDocument()
  })

  it("hydrates an already-running sync from sync.status on open", async () => {
    mockSyncStatus.mockResolvedValue([progress({ posted: 50 })])
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    const line = await screen.findByTestId("apple-mail-sync-progress")
    expect(line.textContent).toMatch(/50\/200 synced/)
  })

  it("shows the completed summary when the sync finishes", async () => {
    renderDetail(<AppleDetail kind="mail" open onClose={vi.fn()} />)
    await screen.findByText(/10 messages/)
    act(() => progressCb?.(progress({ state: "done", posted: 198, skippedFromCursor: 40 })))
    const line = await screen.findByTestId("apple-mail-sync-done")
    expect(line.textContent).toMatch(/198 ingested/)
    expect(line.textContent).toMatch(/40 resumed/)
    expect(line.textContent).toMatch(/2 failed/)
  })
})
