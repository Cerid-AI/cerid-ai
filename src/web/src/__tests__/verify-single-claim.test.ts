// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// E1 CR-019 — the per-claim retry must tell the backend which claim index it is
// re-verifying, so the backend MERGES the fresh verdict into the existing N-claim
// report instead of REPLACING it with a 1-claim report (which wiped the other
// claims' verdicts + broke their feedback indices). This locks the request
// contract: verifySingleClaim sends single_claim_index.

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

import { verifySingleClaim } from "@/lib/api/verification"

function sseBody(events: object[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const ev of events) {
        controller.enqueue(enc.encode(`data: ${JSON.stringify(ev)}\n\n`))
      }
      controller.close()
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("verifySingleClaim (CR-019)", () => {
  it("sends single_claim_index so the backend merges rather than replaces", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: sseBody([
        { type: "claim_verified", status: "verified", confidence: 0.95, verification_method: "expert" },
      ]),
    })

    const result = await verifySingleClaim("The sky is blue.", "conv-123", 3)

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/agent/verify-stream")
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.single_claim_index).toBe(3)
    expect(body.expert_mode).toBe(true)
    expect(body.conversation_id).toBe("conv-123")
    expect(body.response_text).toBe("The sky is blue.")

    expect(result?.status).toBe("verified")
    expect(result?.verification_method).toBe("expert")
  })

  it("preserves index 0 (a valid claim index, not a falsy skip)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: sseBody([{ type: "claim_verified", status: "uncertain", confidence: 0 }]),
    })

    await verifySingleClaim("Claim zero.", "conv-9", 0)

    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string)
    expect(body.single_claim_index).toBe(0)
  })
})
