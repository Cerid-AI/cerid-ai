// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"

const ent = vi.hoisted(() => ({ loading: false, state: "available", error: false }))

vi.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    forFlag: () => ({ state: ent.state }),
    isLoading: ent.loading,
    isError: ent.error,
    tier: "pro",
    forDef: () => ({ state: ent.state }),
  }),
}))

vi.mock("@/lib/api/connectors", () => ({
  startConnectorAuth: vi.fn(async () => ({ auth_kind: "google_oauth", instructions: "Finish in the provider window." })),
}))

vi.mock("@/lib/api/inbox", () => ({
  fetchInboxSetup: vi.fn(),
  addInboxAccount: vi.fn(),
  updateInboxAccount: vi.fn(),
  removeInboxAccount: vi.fn(),
  applyInbox: vi.fn(),
  undoInbox: vi.fn(),
  skipInbox: vi.fn(),
  discoverInbox: vi.fn(),
}))

import { MailSetup } from "@/components/sources/mail-setup"
import { startConnectorAuth } from "@/lib/api/connectors"
import {
  addInboxAccount,
  applyInbox,
  fetchInboxSetup,
  skipInbox,
  undoInbox,
  updateInboxAccount,
} from "@/lib/api/inbox"

const mockSetup = fetchInboxSetup as ReturnType<typeof vi.fn>
const mockAdd = addInboxAccount as ReturnType<typeof vi.fn>
const mockUpdate = updateInboxAccount as ReturnType<typeof vi.fn>
const mockApply = applyInbox as ReturnType<typeof vi.fn>
const mockUndo = undoInbox as ReturnType<typeof vi.fn>
const mockSkip = skipInbox as ReturnType<typeof vi.fn>
const mockConsent = startConnectorAuth as ReturnType<typeof vi.fn>

function emptySetup(overrides: Record<string, unknown> = {}) {
  return {
    actions_enabled: true,
    background_model: "local-background",
    chat_model: "local-chat",
    accounts: [],
    proposals: [],
    recent: [],
    pins: [],
    ...overrides,
  }
}

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  ent.loading = false
  ent.state = "available"
  ent.error = false
  mockSetup.mockResolvedValue(emptySetup())
  mockAdd.mockResolvedValue({})
  mockUpdate.mockResolvedValue({})
  mockApply.mockResolvedValue({ results: [{ ok: true, status: "applied", decision_id: "d1" }] })
  mockUndo.mockResolvedValue({ ok: true, status: "applied" })
  mockSkip.mockResolvedValue({ ok: true, status: "skipped" })
})

describe("MailSetup", () => {
  it("shows an empty address list", async () => {
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByText("No addresses yet.")).toBeInTheDocument()
    expect(screen.getByText("No sender pins yet.")).toBeInTheDocument()
    expect(screen.getByText("Background model: local-background")).toBeInTheDocument()
  })

  it("shows the request error", async () => {
    mockSetup.mockRejectedValue(new Error("inbox request failed: 500"))
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByRole("alert")).toHaveTextContent("inbox request failed: 500")
  })

  it("shows the plan error instead of a community lock", async () => {
    ent.error = true
    ent.state = "locked"
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByTestId("entitlements-unavailable-note")).toBeInTheDocument()
    expect(screen.queryByText("No addresses yet.")).not.toBeInTheDocument()
    expect(mockSetup).not.toHaveBeenCalled()
  })

  it("shows one account and its pending consent", async () => {
    mockSetup.mockResolvedValue(emptySetup({
      accounts: [{
        provider: "gmail",
        address: "other@example.com",
        display_name: "",
        included: true,
        folder_sort: false,
        auto_apply: [],
        utilities: ["correspondence", "financial"],
        consent: "pending",
        removed: false,
        last_read: "",
        last_apply: "",
        last_rejection: "",
      }],
    }))
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByText("other@example.com")).toBeInTheDocument()
    expect(screen.getByText("Pending consent")).toBeInTheDocument()
    expect(screen.queryByText("No addresses yet.")).not.toBeInTheDocument()
  })

  it("adds an address", async () => {
    const user = userEvent.setup()
    render(<MailSetup provider="outlook" />, { wrapper: wrap() })
    await screen.findByText("No addresses yet.")
    await user.type(screen.getByLabelText("Address"), "a@example.com")
    await user.click(screen.getByRole("button", { name: "Add address" }))
    expect(mockAdd).toHaveBeenCalledWith({
      provider: "outlook",
      address: "a@example.com",
      display_name: "",
    })
  })

  it("toggles include for the account", async () => {
    mockSetup.mockResolvedValue(emptySetup({
      accounts: [{
        provider: "outlook",
        address: "a@example.com",
        display_name: "Work",
        included: true,
        folder_sort: false,
        auto_apply: [],
        utilities: ["correspondence"],
        consent: "readonly",
        removed: false,
        last_read: "",
        last_apply: "",
        last_rejection: "",
      }],
    }))
    const user = userEvent.setup()
    render(<MailSetup provider="outlook" />, { wrapper: wrap() })
    await user.click(await screen.findByRole("switch", { name: "Include a@example.com" }))
    expect(mockUpdate).toHaveBeenCalledWith({
      provider: "outlook",
      address: "a@example.com",
      included: false,
    })
  })

  it("approves with dry_run false and can undo", async () => {
    mockSetup.mockResolvedValue(emptySetup({
      proposals: [{
        id: "d1",
        account_address: "a@example.com",
        source: "gmail",
        provider_thread_id: "t1",
        category: "actionable",
        utility: "financial",
        action: "keep",
        band: "local-chat",
        model: "local",
        status: "proposed",
        mailbox_before: "INBOX",
        rag_artifact_ids: [{ id: "fin1", domain: "finance" }],
        draft_body: "The meeting is at 3.",
        classification_reason: "phrase:sale",
      }],
      recent: [{
        id: "d2",
        account_address: "a@example.com",
        source: "gmail",
        provider_thread_id: "t2",
        category: "newsletter",
        utility: "correspondence",
        action: "archive",
        band: "local-small",
        model: "",
        status: "applied",
        mailbox_before: "INBOX",
        rag_artifact_ids: [{ id: "in1", domain: "inbox" }],
      }],
    }))
    const user = userEvent.setup()
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByText("keep · actionable · local-chat · local")).toBeInTheDocument()
    expect(screen.getByText("phrase:sale")).toBeInTheDocument()
    expect(screen.getByText("The meeting is at 3.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Finance card" })).toHaveAttribute("href", "/?artifact=fin1")
    expect(screen.getByRole("link", { name: "Correspondence" })).toHaveAttribute("href", "/?artifact=in1")
    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect(mockApply).toHaveBeenCalledWith(["d1"], false)
    await user.click(screen.getByRole("button", { name: "Undo" }))
    expect(mockUndo).toHaveBeenCalledWith("d2", false)
  })

  it("keeps skip available when mailbox writes are off", async () => {
    mockSetup.mockResolvedValue(emptySetup({
      actions_enabled: false,
      proposals: [{
        id: "d1",
        account_address: "a@example.com",
        source: "gmail",
        provider_thread_id: "t1",
        category: "promo",
        utility: "none",
        action: "archive",
        band: "skip",
        model: "",
        status: "proposed",
        mailbox_before: "",
        rag_artifact_ids: [],
      }],
      recent: [{
        id: "d2",
        account_address: "a@example.com",
        source: "gmail",
        provider_thread_id: "t2",
        category: "promo",
        utility: "none",
        action: "archive",
        band: "",
        model: "",
        status: "applied",
        mailbox_before: "",
        rag_artifact_ids: [],
      }],
    }))
    const user = userEvent.setup()
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(await screen.findByRole("button", { name: "Approve" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled()
    const skip = screen.getByRole("button", { name: "Skip" })
    expect(skip).toBeEnabled()
    await user.click(skip)
    expect(mockSkip).toHaveBeenCalledWith("d1")
    expect(screen.getByText(/recreate the Gmail and Outlook connector containers/)).toBeInTheDocument()
  })

  it("does not show an upgrade pitch while entitlements are loading", () => {
    ent.loading = true
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    expect(screen.queryByTestId("mail-setup")).not.toBeInTheDocument()
    expect(screen.queryByText(/unlock/i)).not.toBeInTheDocument()
    expect(mockSetup).not.toHaveBeenCalled()
  })

  it("starts the existing connector consent flow", async () => {
    const user = userEvent.setup()
    render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    await user.click(await screen.findByRole("button", { name: "Re-consent" }))
    expect(mockConsent).toHaveBeenCalledWith("gmail")
    expect(await screen.findByText("Finish in the provider window.")).toBeInTheDocument()
  })

  it("is axe-clean for a single account", async () => {
    mockSetup.mockResolvedValue(emptySetup({
      accounts: [{
        provider: "gmail",
        address: "a@example.com",
        display_name: "Personal",
        included: true,
        folder_sort: false,
        auto_apply: [],
        utilities: ["correspondence", "financial"],
        consent: "readonly",
        removed: false,
        last_read: "",
        last_apply: "",
        last_rejection: "",
      }],
    }))
    const { container } = render(<MailSetup provider="gmail" />, { wrapper: wrap() })
    await screen.findByText("Personal")
    expect(await axe(container)).toHaveNoViolations()
  })
})
