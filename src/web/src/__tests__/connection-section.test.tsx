// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The desktop client's server connection, as onboarding and Settings both
// mount it. Two behaviours matter more than the plumbing:
//
//   1. It PROBES before it asks. A first-run user cannot answer "do you need an
//      API key?" — the server can. Asking unconditionally is a question with no
//      knowable answer; asking when nothing is even listening points at the
//      wrong problem entirely.
//   2. The key field exists in LOCAL mode. It used to render only inside
//      `{remote && (...)}`, so a local-mode client had no way to send a key
//      from anywhere in the product while a local server enforced auth exactly
//      as a remote one does — every request 401'd and the wizard blamed Docker.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ConnectionSection } from "@/components/settings/connection-section"
import { probeStateFrom } from "@/components/settings/server-connection-form"

const get = vi.fn()
const set = vi.fn()
const test = vi.fn()

function installBridge() {
  ;(window as unknown as { cerid?: unknown }).cerid = { connection: { get, set, test } }
}

/** The server accepts us. */
function connected() {
  test.mockResolvedValue({ ok: true, detail: "Connected (HTTP 200)", auth: "ok" })
}
/** The server answered 401/403. */
function needsKey() {
  test.mockResolvedValue({ ok: false, detail: "This server requires an API key", auth: "required" })
}
/** Nothing is listening. */
function unreachable() {
  test.mockResolvedValue({ ok: false, detail: "connect ECONNREFUSED", auth: "unknown" })
}

beforeEach(() => {
  get.mockReset().mockResolvedValue({ mode: "local", serverUrl: "http://localhost:8888", hasApiKey: false })
  set.mockReset().mockResolvedValue({ mode: "remote", serverUrl: "https://macpro.local", hasApiKey: true })
  test.mockReset()
  connected()
})

afterEach(() => {
  delete (window as unknown as { cerid?: unknown }).cerid
  vi.restoreAllMocks()
})

describe("probeStateFrom", () => {
  it("asks for a key only when the server demanded one", () => {
    expect(probeStateFrom({ ok: false, auth: "required" })).toBe("needs-key")
  })

  it("reports connected when the authenticated probe succeeded", () => {
    expect(probeStateFrom({ ok: true, auth: "ok" })).toBe("connected")
  })

  it("does not ask for a key when nothing is listening", () => {
    // The key is not the problem, and prompting for one sends the user off to
    // hunt a credential when the server simply is not running.
    expect(probeStateFrom({ ok: false, auth: "unknown", detail: "ECONNREFUSED" })).toBe("unreachable")
  })

  it("never reports connected on an unanswerable probe", () => {
    // "We did not observe a refusal" is not acceptance.
    expect(probeStateFrom({ ok: false, auth: "unknown" })).not.toBe("connected")
    expect(probeStateFrom({ ok: false })).not.toBe("connected")
  })
})

describe("ConnectionSection", () => {
  it("renders nothing in the browser build (no desktop bridge)", () => {
    const { container } = render(<ConnectionSection />)
    expect(container).toBeEmptyDOMElement()
  })

  it("collapses to a confirmation when the server accepts us", async () => {
    // No credential prompt at all — there is nothing to ask.
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-ok")
    expect(screen.getByText(/Connected to/)).toBeInTheDocument()
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument()
  })

  it("expands on request so the server can still be changed", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-ok")
    await userEvent.click(screen.getByRole("button", { name: "Change server" }))
    expect(await screen.findByLabelText("API key")).toBeInTheDocument()
  })
})

describe("ConnectionSection — the server asked for a key", () => {
  it("shows the key field and says where the value comes from", async () => {
    needsKey()
    installBridge()
    render(<ConnectionSection />)

    await screen.findByTestId("connection-needs-key")
    expect(screen.getByText(/CERID_API_KEY/)).toBeInTheDocument()
    // In LOCAL mode — the whole point. No mode switch needed.
    expect(screen.queryByLabelText("Server URL")).not.toBeInTheDocument()
    expect(screen.getByLabelText("API key")).toBeInTheDocument()
  })

  it("saves a key entered in local mode", async () => {
    needsKey()
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-needs-key")

    await userEvent.type(screen.getByLabelText("API key"), "sekrit")
    await userEvent.click(screen.getByTestId("connection-save"))

    await waitFor(() =>
      expect(set).toHaveBeenCalledWith(expect.objectContaining({ mode: "local", apiKey: "sekrit" })), // pragma: allowlist secret
    )
  })

  it("re-probes localhost with the entered key", async () => {
    needsKey()
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-needs-key")

    await userEvent.type(screen.getByLabelText("API key"), "sekrit")
    await userEvent.click(screen.getByTestId("connection-test"))

    await waitFor(() =>
      expect(test).toHaveBeenLastCalledWith({
        serverUrl: "http://localhost:8888",
        apiKey: "sekrit", // pragma: allowlist secret
      }),
    )
  })

  it("keeps the stored key when the field is left untouched", async () => {
    needsKey()
    get.mockResolvedValue({ mode: "local", serverUrl: "http://localhost:8888", hasApiKey: true })
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-needs-key")

    await userEvent.click(screen.getByTestId("connection-save"))
    await waitFor(() => expect(set).toHaveBeenCalled())
    // No apiKey property at all — sending "" would ERASE the stored key.
    expect(set.mock.calls[0][0]).not.toHaveProperty("apiKey")
  })
})

describe("ConnectionSection — nothing is listening", () => {
  it("explains how to start the stack instead of asking for a credential", async () => {
    unreachable()
    installBridge()
    render(<ConnectionSection />)

    await screen.findByTestId("connection-unreachable")
    expect(screen.getByText(/start-cerid\.sh/)).toBeInTheDocument()
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument()
  })
})

describe("ConnectionSection — remote mode", () => {
  it("reveals the address field when switched", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-ok")
    await userEvent.click(screen.getByRole("button", { name: "Change server" }))
    await userEvent.click(screen.getByRole("radio", { name: "Remote server" }))
    expect(await screen.findByLabelText("Server URL")).toBeInTheDocument()
  })

  it("blocks save on an invalid remote URL", async () => {
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-ok")
    await userEvent.click(screen.getByRole("button", { name: "Change server" }))
    await userEvent.click(screen.getByRole("radio", { name: "Remote server" }))
    await userEvent.type(screen.getByLabelText("Server URL"), "not-a-url")
    expect(screen.getByTestId("connection-save")).toBeDisabled()
  })

  it("saves the remote connection with the key omitted when left blank", async () => {
    get.mockResolvedValue({ mode: "remote", serverUrl: "https://macpro.local", hasApiKey: true })
    installBridge()
    render(<ConnectionSection />)
    await screen.findByTestId("connection-ok")
    await userEvent.click(screen.getByRole("button", { name: "Change server" }))

    await userEvent.click(screen.getByTestId("connection-save"))
    await waitFor(() =>
      expect(set).toHaveBeenCalledWith({ mode: "remote", serverUrl: "https://macpro.local" }),
    )
  })
})
