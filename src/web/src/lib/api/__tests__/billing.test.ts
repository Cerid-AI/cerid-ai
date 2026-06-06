// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { openBillingPortal } from "@/lib/api/billing"

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
