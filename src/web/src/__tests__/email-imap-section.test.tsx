// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

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

import { EmailImapSection } from "@/components/sources/email-imap-section"

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <EmailImapSection />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  configureEmail.mockReset().mockResolvedValue({ status: "configured", host: "h", user: "u" })
  fetchEmailStatus.mockReset().mockResolvedValue({ last_poll: null, messages_ingested: 0, errors: [] })
  pollEmailNow.mockReset().mockResolvedValue({ status: "ok", messages: 0 })
  deleteEmailSource.mockReset().mockResolvedValue({ status: "deleted" })
  notifyError.mockReset()
})
afterEach(() => vi.restoreAllMocks())

describe("EmailImapSection", () => {
  it("disables Connect until host/user/password are filled", async () => {
    renderSection()
    const save = await screen.findByTestId("email-save")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    expect(save).toBeDisabled() // still missing password
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    expect(save).toBeEnabled()
  })

  it("submits the config on Connect", async () => {
    renderSection()
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    await userEvent.click(screen.getByTestId("email-save"))
    await waitFor(() =>
      expect(configureEmail).toHaveBeenCalledWith(
        expect.objectContaining({ host: "imap.example.com", user: "me@example.com", password: "pw" }), // pragma: allowlist secret
      ),
    )
  })

  it("gates disconnect behind a confirmation dialog", async () => {
    fetchEmailStatus.mockResolvedValue({ last_poll: "2026-06-19T00:00:00Z", messages_ingested: 2, errors: [] })
    renderSection()
    // "connected" state → disconnect button present
    const disconnect = await screen.findByTestId("email-disconnect")
    await userEvent.click(disconnect)
    // Not deleted yet — confirmation required
    expect(deleteEmailSource).not.toHaveBeenCalled()
    await userEvent.click(await screen.findByTestId("email-disconnect-confirm"))
    await waitFor(() => expect(deleteEmailSource).toHaveBeenCalledTimes(1))
  })

  it("surfaces a configure failure via notifyError", async () => {
    configureEmail.mockRejectedValue(new Error("login failed"))
    renderSection()
    await userEvent.type(screen.getByLabelText("IMAP host"), "imap.example.com")
    await userEvent.type(screen.getByLabelText("Username"), "me@example.com")
    await userEvent.type(screen.getByLabelText("Password"), "pw")
    await userEvent.click(screen.getByTestId("email-save"))
    await waitFor(() => expect(notifyError).toHaveBeenCalled())
  })
})
