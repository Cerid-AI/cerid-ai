// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Task A1: a tested key is not a saved key. Evidence (live smoke, packaged
// 1.0.4): after typing the key and pressing Test connection, a successful
// probe collapsed the form before Save & reconnect was ever clicked, so
// bridge.set never ran and connection.json was never written — every
// authenticated call then 401'd and the wizard reappeared on relaunch.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ServerConnectionForm } from "@/components/settings/server-connection-form"

const get = vi.fn()
const set = vi.fn()
const test = vi.fn()

function installBridge() {
  ;(window as unknown as { cerid?: unknown }).cerid = { connection: { get, set, test } }
}

beforeEach(() => {
  get.mockReset().mockResolvedValue({
    mode: "local",
    serverUrl: "http://localhost:8888",
    hasApiKey: false,
  })
  set.mockReset().mockResolvedValue({
    mode: "local",
    serverUrl: "http://localhost:8888",
    hasApiKey: true,
  })
  test.mockReset()
})

afterEach(() => {
  delete (window as unknown as { cerid?: unknown }).cerid
  vi.restoreAllMocks()
})

describe("ServerConnectionForm — a tested key is not a saved key", () => {
  it("keeps the form open after a successful probe with an unsaved key", async () => {
    // Mount probe (no key typed yet): the server asks for one.
    test.mockResolvedValueOnce({
      ok: false,
      detail: "This server requires an API key",
      auth: "required",
    })
    installBridge()
    render(<ServerConnectionForm />)
    await screen.findByTestId("connection-needs-key")

    // The user pastes a key and it tests clean.
    test.mockResolvedValueOnce({ ok: true, detail: "Connected (HTTP 200)", auth: "ok" })
    await userEvent.type(screen.getByLabelText("API key"), "sekrit")
    await userEvent.click(screen.getByTestId("connection-test"))

    await screen.findByText("Save to keep this key on this Mac")
    expect(screen.queryByTestId("connection-ok")).not.toBeInTheDocument()
    expect(screen.getByTestId("connection-save")).toBeEnabled()
  })

  it("collapses to the confirmed view for a stored, already-connected server", async () => {
    test.mockResolvedValue({ ok: true, detail: "Connected (HTTP 200)", auth: "ok" })
    installBridge()
    render(<ServerConnectionForm />)

    await screen.findByTestId("connection-ok")
    expect(screen.getByText(/Connected to/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Change server" })).toBeInTheDocument()
  })
})

describe("ServerConnectionForm — the needs-key message before any attempt", () => {
  it("renders muted helper text on mount, not the destructive alert", async () => {
    test.mockResolvedValue({
      ok: false,
      detail: "This server requires an API key",
      auth: "required",
    })
    installBridge()
    render(<ServerConnectionForm />)

    await screen.findByTestId("connection-needs-key")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("shows the alert once the user has attempted a connection", async () => {
    test.mockResolvedValue({
      ok: false,
      detail: "This server requires an API key",
      auth: "required",
    })
    installBridge()
    render(<ServerConnectionForm />)
    await screen.findByTestId("connection-needs-key")

    await userEvent.click(screen.getByTestId("connection-test"))
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0))
  })
})

describe("ServerConnectionForm — save", () => {
  it("calls bridge.set with mode, serverUrl and the typed key", async () => {
    test.mockResolvedValue({
      ok: false,
      detail: "This server requires an API key",
      auth: "required",
    })
    installBridge()
    render(<ServerConnectionForm />)
    await screen.findByTestId("connection-needs-key")

    await userEvent.type(screen.getByLabelText("API key"), "sekrit")
    await userEvent.click(screen.getByTestId("connection-save"))

    await waitFor(() =>
      expect(set).toHaveBeenCalledWith({
        mode: "local",
        serverUrl: "http://localhost:8888",
        apiKey: "sekrit", // pragma: allowlist secret
      }),
    )
  })
})
