// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// The web container's proxy answers 401 with X-Cerid-Auth when the caller has
// not signed in, and names the port's state on every response it forwards.
// The API's own 401 (a bad key, an expired token) carries neither.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { toast } from "sonner"
import { getPortAccess, watchWebPort } from "@/lib/web-port"

vi.mock("sonner", () => ({ toast: { error: vi.fn() } }))

const signInRequired = () =>
  new Response('{"detail":"Sign in to use Cerid on this port."}', {
    status: 401,
    headers: { "X-Cerid-Auth": "sign-in-required" },
  })

function install(responses: Array<() => Response>) {
  const upstream = vi.fn(async () => responses.shift()!())
  const navigate = vi.fn()
  const target = { fetch: upstream as unknown as typeof fetch }
  watchWebPort({ target, navigate })
  return { send: target.fetch, upstream, navigate }
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(toast.error).mockClear()
  window.history.replaceState(null, "", "/?pane=settings#models")
})

afterEach(() => {
  vi.useRealTimers()
})

describe("watchWebPort", () => {
  it("sends the browser to the sign-in page, and back to where it was", async () => {
    const { send, navigate } = install([signInRequired])

    const res = await send("/api/mcp/settings")

    expect(res.status).toBe(401)
    expect(navigate).toHaveBeenCalledExactlyOnceWith(
      `/__auth/login?next=${encodeURIComponent("/?pane=settings#models")}`,
    )
  })

  it("navigates once when many requests are refused together", async () => {
    const { send, navigate } = install([signInRequired, signInRequired, signInRequired])

    await Promise.all([send("/api/mcp/a"), send("/api/mcp/b"), send("/api/mcp/c")])

    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it("does not loop when the page comes straight back still refused", async () => {
    await install([signInRequired]).send("/api/mcp/settings")

    // The sign-in page returned here and the page loaded afresh.
    const second = install([signInRequired])
    await second.send("/api/mcp/settings")

    expect(second.navigate).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledOnce()
    const [, options] = vi.mocked(toast.error).mock.calls[0]
    const action = options?.action as { label: string; onClick: () => void }
    expect(action.label).toBe("Sign in")
    action.onClick()
    expect(second.navigate).toHaveBeenCalledOnce()
  })

  it("navigates again once a request has succeeded in between", async () => {
    const first = install([signInRequired])
    await first.send("/api/mcp/settings")

    const second = install([
      () => new Response("{}", { status: 200, headers: { "X-Cerid-Port-Access": "signed-in" } }),
      signInRequired,
    ])
    await second.send("/api/mcp/settings")
    await second.send("/api/mcp/settings")

    expect(second.navigate).toHaveBeenCalledOnce()
  })

  it("leaves the API's own 401 to the caller", async () => {
    const { send, navigate } = install([
      () => new Response('{"detail":"Invalid or missing API key"}', { status: 401 }),
    ])

    const res = await send("/api/mcp/settings")

    expect(res.status).toBe(401)
    expect(await res.json()).toEqual({ detail: "Invalid or missing API key" })
    expect(navigate).not.toHaveBeenCalled()
  })

  it("passes arguments and the response through untouched", async () => {
    const { send, upstream } = install([() => new Response('{"ok":true}', { status: 200 })])
    const init = { method: "POST", body: "{}" }

    const res = await send("/api/mcp/query", init)

    expect(upstream).toHaveBeenCalledWith("/api/mcp/query", init)
    expect(await res.json()).toEqual({ ok: true })
  })

  it("records that the port is open when the proxy says so", async () => {
    const { send } = install([
      () => new Response("{}", { status: 200, headers: { "X-Cerid-Port-Access": "open" } }),
      () => new Response("{}", { status: 200, headers: { "X-Cerid-Port-Access": "signed-in" } }),
      () => new Response("{}", { status: 200 }),
    ])

    await send("/api/mcp/health/status")
    expect(getPortAccess()).toBe("open")

    await send("/api/mcp/health/status")
    expect(getPortAccess()).toBe("signed-in")

    // A response that did not come through the proxy says nothing either way.
    await send("/version.json")
    expect(getPortAccess()).toBe("signed-in")
  })
})
