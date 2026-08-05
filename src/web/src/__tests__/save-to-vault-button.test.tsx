// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * SaveToVaultButton — RAG C3.4 chat-side vault-write UI.
 *
 * Covers:
 *   - Button renders inline (visible affordance for assistant messages).
 *   - Dialog opens on click, vault selector populates from mocked API.
 *   - Save action POSTs the right WriteNoteRequest shape.
 *   - Success toast fires + dialog closes; error toast fires on failure.
 *   - Default filename derived from conversationTitle + messageId.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks — declared before the component import (vitest hoists them).
// ---------------------------------------------------------------------------

const mockFetchVaults = vi.fn()
const mockWriteNote = vi.fn()

vi.mock("@/lib/api/wiki", () => ({
  fetchVaultsList: (...args: unknown[]) => mockFetchVaults(...args),
  writeNote: (...args: unknown[]) => mockWriteNote(...args),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}))

import { SaveToVaultButton } from "@/components/chat/save-to-vault-button"
import { toast } from "sonner"

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const vaultsFixture = [
  {
    id: "vault-1",
    path: "/archive/main",
    label: "Main Vault",
    enabled: true,
    domain_override: null,
    exclude_patterns: [],
    search_enabled: true,
    is_vault: true,
    last_scanned_at: null,
    stats: { ingested: 0, skipped: 0, errored: 0 },
    created_at: "2026-01-01T00:00:00Z",
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchVaults.mockResolvedValue(vaultsFixture)
  mockWriteNote.mockResolvedValue({
    file_path: "/archive/main/chat/foo.md",
    artifact_id: "art-1",
    ingested: true,
    frontmatter_written: {},
    mode: "create",
    reingest_error: null,
  })
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SaveToVaultButton", () => {
  it("renders the icon trigger", () => {
    render(
      <SaveToVaultButton content="Hello world" messageId="msg-abc12345" />,
      { wrapper },
    )
    expect(screen.getByLabelText("Save to vault")).toBeTruthy()
  })

  it("opens the dialog and loads vaults on click", async () => {
    render(
      <SaveToVaultButton content="Hello world" messageId="msg-abc12345" />,
      { wrapper },
    )
    await userEvent.click(screen.getByLabelText("Save to vault"))
    expect(await screen.findByRole("dialog")).toBeTruthy()
    await waitFor(() => {
      expect(mockFetchVaults).toHaveBeenCalled()
    })
  })

  it("calls writeNote with the request payload on Save", async () => {
    render(
      <SaveToVaultButton
        content="Hello world"
        messageId="msg-abc12345"
        conversationTitle="My Chat"
      />,
      { wrapper },
    )
    await userEvent.click(screen.getByLabelText("Save to vault"))
    await screen.findByRole("dialog")
    // Wait for vaults to populate so the Save button enables.
    await waitFor(() => {
      expect(mockFetchVaults).toHaveBeenCalled()
    })

    const saveBtn = await screen.findByRole("button", { name: /^Save$/ })
    await waitFor(() => expect(saveBtn).not.toBeDisabled())
    await userEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockWriteNote).toHaveBeenCalledTimes(1)
    })
    const req = mockWriteNote.mock.calls[0][0]
    expect(req.vault_id).toBe("vault-1")
    expect(req.content).toBe("Hello world")
    expect(req.mode).toBe("create")
    expect(req.allow_synthesis_input).toBe(false)
    // Default path uses slug(conversationTitle) + shortId(messageId).
    expect(req.path).toBe("chat/my-chat-msgabc12.md")
    expect(req.frontmatter).toEqual({
      "cerid:source_message_id": "msg-abc12345",
    })
  })

  it("fires a success toast and closes the dialog after a successful save", async () => {
    render(
      <SaveToVaultButton content="Hi" messageId="m1" />,
      { wrapper },
    )
    await userEvent.click(screen.getByLabelText("Save to vault"))
    await screen.findByRole("dialog")
    await waitFor(() => expect(mockFetchVaults).toHaveBeenCalled())

    const saveBtn = await screen.findByRole("button", { name: /^Save$/ })
    await waitFor(() => expect(saveBtn).not.toBeDisabled())
    await userEvent.click(saveBtn)

    await waitFor(() => {
      expect((toast.success as ReturnType<typeof vi.fn>)).toHaveBeenCalled()
    })
    // Dialog dismissed.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull()
    })
  })

  it("fires an error toast and keeps the dialog open on failure", async () => {
    mockWriteNote.mockRejectedValueOnce(new Error("Path conflicts with existing file"))
    render(
      <SaveToVaultButton content="Hi" messageId="m1" />,
      { wrapper },
    )
    await userEvent.click(screen.getByLabelText("Save to vault"))
    await screen.findByRole("dialog")
    await waitFor(() => expect(mockFetchVaults).toHaveBeenCalled())

    const saveBtn = await screen.findByRole("button", { name: /^Save$/ })
    await waitFor(() => expect(saveBtn).not.toBeDisabled())
    await userEvent.click(saveBtn)

    await waitFor(() => {
      expect((toast.error as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(
        "Path conflicts with existing file",
      )
    })
    // Dialog still mounted.
    expect(screen.queryByRole("dialog")).not.toBeNull()
  })
})
