// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The setup card collapsed every failure into "Could not reach backend — is
// Docker running?". A packaged desktop app with no API key stored gets a 401
// from a perfectly healthy stack, so that message sent an operator to check
// Docker while Docker was fine and the real fix was three menus away.
//
// The status has to survive the API layer for this to be decidable at all:
// `throw new Error("System check failed")` discarded it, so a refusal and a
// dead socket arrived at the catch block identical.

import { describe, expect, it, vi } from "vitest"
import { authFailureMessage } from "@/components/setup/system-check-card"
import { SystemCheckHttpError } from "@/lib/api/settings"

describe("authFailureMessage", () => {
  it("names authentication on a 401 and says Docker is not the problem", () => {
    const msg = authFailureMessage(new SystemCheckHttpError(401, "Invalid or missing API key"))
    expect(msg).toContain("401")
    expect(msg).toMatch(/api key/i)
    expect(msg).toMatch(/Docker is fine/i)
  })

  it("treats 403 the same way", () => {
    expect(authFailureMessage(new SystemCheckHttpError(403, "Forbidden"))).toMatch(/api key/i)
  })

  it("returns null for a server error, which is NOT an auth problem", () => {
    // 500 means the server is there and broken. Claiming the key is wrong
    // would be the same substitution in the opposite direction.
    expect(authFailureMessage(new SystemCheckHttpError(500, "boom"))).toBeNull()
  })

  it("returns null for a transport failure, so the Docker hint still shows", () => {
    // fetch() rejects with a plain TypeError when the socket is dead — that IS
    // the unreachable case the original message was written for.
    expect(authFailureMessage(new TypeError("Failed to fetch"))).toBeNull()
    expect(authFailureMessage(new Error("System check failed"))).toBeNull()
    expect(authFailureMessage(undefined)).toBeNull()
  })
})

describe("fetchSystemCheck carries the status", () => {
  // Without this the message test above is vacuous: it builds the error by
  // hand, so reverting the throw site to `new Error("System check failed")`
  // leaves it green while the real path loses the status again. The plant
  // proved exactly that.
  it("throws SystemCheckHttpError with the status on a 401", async () => {
    const { fetchSystemCheck } = await import("@/lib/api/settings")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ detail: "Invalid or missing API key" }),
    }))

    const err = await fetchSystemCheck().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(SystemCheckHttpError)
    expect((err as SystemCheckHttpError).status).toBe(401)
    expect(authFailureMessage(err)).toMatch(/api key/i)
    vi.unstubAllGlobals()
  })

  it("resolves normally on success", async () => {
    const { fetchSystemCheck } = await import("@/lib/api/settings")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ os: "darwin" }),
    }))
    await expect(fetchSystemCheck()).resolves.toMatchObject({ os: "darwin" })
    vi.unstubAllGlobals()
  })
})
