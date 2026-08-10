// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { openBillingPortal } from "@/lib/api/billing"

/** Fresh module instance — fetchCapabilities memoises the discovered edition. */
async function freshApi() {
  vi.resetModules()
  return import("@/lib/api/billing")
}

function reply(ok: boolean, status: number, body: unknown) {
  return { ok, status, json: async () => body, text: async () => JSON.stringify(body) }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("openBillingPortal", () => {
  it("POSTs to /billing/portal and returns the portal_url", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ portal_url: "https://billing.stripe.com/p/test" }),
    })
    vi.stubGlobal("fetch", fetchMock)

    const url = await openBillingPortal({ returnUrl: "http://localhost/settings" })

    expect(url).toBe("https://billing.stripe.com/p/test")
    const [calledUrl, opts] = fetchMock.mock.calls[0]
    expect(String(calledUrl)).toContain("/billing/portal")
    expect(opts.method).toBe("POST")
    expect(opts.headers["X-Client-ID"]).toBe("gui")
    expect(JSON.parse(opts.body).return_url).toBe("http://localhost/settings")
  })

  it("throws on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: "No active subscription" }),
      }),
    )
    await expect(openBillingPortal()).rejects.toThrow()
  })
})


// The community server has no billing router; the commercial one has no
// /license router. One client has to work against both, and getting this wrong
// leaves every Pro surface locked forever in the open-core build.
describe("fetchCapabilities — edition discovery", () => {
  it("uses the commercial endpoint when it answers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      reply(true, 200, { tier: "pro", features: {}, buckets: {} }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const { fetchCapabilities } = await freshApi()

    expect((await fetchCapabilities()).tier).toBe("pro")
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain("/billing/capabilities")
  })

  it("falls back to the community endpoint on 404", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(false, 404, { detail: "Not Found" }))
      .mockResolvedValueOnce(reply(true, 200, { tier: "community", features: {}, buckets: {} }))
    vi.stubGlobal("fetch", fetchMock)
    const { fetchCapabilities } = await freshApi()

    expect((await fetchCapabilities()).tier).toBe("community")
    expect(String(fetchMock.mock.calls[1][0])).toContain("/license/capabilities")
  })

  it("does not re-probe once the edition is known", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(false, 404, {}))
      .mockResolvedValue(reply(true, 200, { tier: "community", features: {}, buckets: {} }))
    vi.stubGlobal("fetch", fetchMock)
    const { fetchCapabilities } = await freshApi()

    await fetchCapabilities()
    await fetchCapabilities()

    // 404 probe + first success + second call straight to the known path.
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(String(fetchMock.mock.calls[2][0])).toContain("/license/capabilities")
  })

  it("does not treat a server error as the wrong edition", async () => {
    // A 500 on the endpoint that DOES exist must surface, not silently
    // redirect to an endpoint that will answer for a different reason.
    const fetchMock = vi.fn().mockResolvedValue(reply(false, 500, { detail: "boom" }))
    vi.stubGlobal("fetch", fetchMock)
    const { fetchCapabilities } = await freshApi()

    await expect(fetchCapabilities()).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
