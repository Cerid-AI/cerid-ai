// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ConnectionSection } from "@/components/settings/connection-section"

const get = vi.fn()
const set = vi.fn()
const test = vi.fn()

function installBridge() {
  ;(window as unknown as { cerid?: unknown }).cerid = { connection: { get, set, test } }
}

beforeEach(() => {
  get.mockReset().mockResolvedValue({ mode: "local", serverUrl: "http://localhost:8888", hasApiKey: false })
  set.mockReset().mockResolvedValue({ mode: "remote", serverUrl: "https://macpro.local", hasApiKey: true })
  test.mockReset().mockResolvedValue({ ok: true, detail: "HTTP 200" })
})

afterEach(() => {
  delete (window as unknown as { cerid?: unknown }).cerid
  vi.restoreAllMocks()
})

describe("ConnectionSection", () => {
  it("renders nothing in the browser build (no desktop bridge)", () => {
    const { container } = render(<ConnectionSection />)
    expect(container).toBeEmptyDOMElement()
  })

  it("hydrates from the desktop bridge and reveals remote fields when switched", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-section")
    expect(get).toHaveBeenCalled()
    // Local mode → no server URL field
    expect(screen.queryByLabelText("Server URL")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("radio", { name: "Remote server" }))
    expect(await screen.findByLabelText("Server URL")).toBeInTheDocument()
  })

  it("tests the entered remote target", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-section")
    await userEvent.click(screen.getByRole("radio", { name: "Remote server" }))
    await userEvent.type(screen.getByLabelText("Server URL"), "https://macpro.local")
    await userEvent.type(screen.getByLabelText("API key"), "k")
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }))
    await waitFor(() =>
      expect(test).toHaveBeenCalledWith({ serverUrl: "https://macpro.local", apiKey: "k" }),
    )
    expect(await screen.findByText("HTTP 200")).toBeInTheDocument()
  })

  it("saves the remote connection (key omitted when left blank)", async () => {
    installBridge()
    get.mockResolvedValue({ mode: "remote", serverUrl: "https://macpro.local", hasApiKey: true })
    render(<ConnectionSection />)
    await screen.findByTestId("connection-section")
    await waitFor(() => expect(screen.getByLabelText("Server URL")).toHaveValue("https://macpro.local"))
    await userEvent.click(screen.getByTestId("connection-save"))
    await waitFor(() =>
      expect(set).toHaveBeenCalledWith({ mode: "remote", serverUrl: "https://macpro.local" }),
    )
  })

  it("blocks save on an invalid remote URL", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-section")
    await userEvent.click(screen.getByRole("radio", { name: "Remote server" }))
    await userEvent.type(screen.getByLabelText("Server URL"), "not-a-url")
    expect(screen.getByTestId("connection-save")).toBeDisabled()
  })
})
