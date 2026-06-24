// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

const configureEmail = vi.fn()
const fetchEmailStatus = vi.fn()
const pollEmailNow = vi.fn()
const deleteEmailSource = vi.fn()

vi.mock("@/lib/api/email", () => ({
  configureEmail: (...a: unknown[]) => configureEmail(...a),
  fetchEmailStatus: (...a: unknown[]) => fetchEmailStatus(...a),
  pollEmailNow: (...a: unknown[]) => pollEmailNow(...a),
  deleteEmailSource: (...a: unknown[]) => deleteEmailSource(...a),
}))

const notifyError = vi.fn()
vi.mock("@/lib/query-client", () => ({ notifyError: (...a: unknown[]) => notifyError(...a) }))

import { EmailDetail } from "@/components/sources/email-detail"

function renderDetail(open = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onClose = vi.fn()
  const utils = render(
    <QueryClientProvider client={qc}>
      <EmailDetail open={open} onClose={onClose} />
    </QueryClientProvider>,
  )
  return { ...utils, onClose, qc }
}

beforeEach(() => {
  configureEmail.mockReset().mockResolvedValue({ status: "configured", host: "h", user: "u" })
  fetchEmailStatus.mockReset().mockResolvedValue({ last_poll: null, messages_ingested: 0, errors: [] })
  pollEmailNow.mockReset().mockResolvedValue({ status: "ok", messages: 0 })
  deleteEmailSource.mockReset().mockResolvedValue({ status: "deleted" })
  notifyError.mockReset()
})
afterEach(() => vi.restoreAllMocks())

describe("EmailDetail", () => {
  it("renders the IMAP form inside a dialog", async () => {
    renderDetail()
    expect(await screen.findByLabelText("IMAP host")).toBeInTheDocument()
    expect(screen.getByLabelText("Username")).toBeInTheDocument()
    expect(screen.getByLabelText("Password")).toBeInTheDocument()
  })

  it("disables Connect until host/user/password are filled", async () => {
    renderDetail()
    const save = await screen.findByTestId("email-save")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    expect(save).toBeEnabled()
  })

  it("calls configureEmail on Connect and invalidates both queries", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(qc, "invalidateQueries")
    render(
      <QueryClientProvider client={qc}>
        <EmailDetail open onClose={vi.fn()} />
      </QueryClientProvider>,
    )
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    await userEvent.click(screen.getByTestId("email-save"))
    await waitFor(() => expect(configureEmail).toHaveBeenCalledWith(
      expect.objectContaining({ host: "imap.example.com", user: "me@example.com", password: "pw" }), // pragma: allowlist secret
    ))
    await waitFor(() => {
      const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey[0])
      expect(keys).toContain("email-status")
      expect(keys).toContain("ingestion-sources")
    })
  })

  it("calls pollEmailNow on Poll now", async () => {
    fetchEmailStatus.mockResolvedValue({ last_poll: "2026-06-19T00:00:00Z", messages_ingested: 2, errors: [] })
    renderDetail()
    // Wait for the query to resolve and enable the button (hasActivity becomes true once status lands)
    const pollBtn = await screen.findByTestId("email-poll-now")
    await waitFor(() => expect(pollBtn).toBeEnabled())
    await userEvent.click(pollBtn)
    await waitFor(() => expect(pollEmailNow).toHaveBeenCalledTimes(1))
  })

  it("gates disconnect behind a confirmation dialog and calls deleteEmailSource", async () => {
    fetchEmailStatus.mockResolvedValue({ last_poll: "2026-06-19T00:00:00Z", messages_ingested: 2, errors: [] })
    renderDetail()
    const disconnect = await screen.findByTestId("email-disconnect")
    await userEvent.click(disconnect)
    expect(deleteEmailSource).not.toHaveBeenCalled()
    await userEvent.click(await screen.findByTestId("email-disconnect-confirm"))
    await waitFor(() => expect(deleteEmailSource).toHaveBeenCalledTimes(1))
  })

  it("surfaces a configure failure via notifyError", async () => {
    configureEmail.mockRejectedValue(new Error("login failed"))
    renderDetail()
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    await userEvent.click(screen.getByTestId("email-save"))
    await waitFor(() => expect(notifyError).toHaveBeenCalled())
  })

  it("is axe-clean when open", async () => {
    const { container } = renderDetail()
    await screen.findByLabelText("IMAP host")
    expect(await axe(container)).toHaveNoViolations()
  })
})
