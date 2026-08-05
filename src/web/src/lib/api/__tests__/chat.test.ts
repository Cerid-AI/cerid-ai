// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { ingestFeedback, streamChat } from "@/lib/api/chat"

beforeEach(() => vi.clearAllMocks())

describe("streamChat", () => {
  it("CR-026: forwards cost_sensitivity from chatSettings to the request payload", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("data: [DONE]\n\n"))
        c.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    vi.stubGlobal("fetch", fetchMock)

    await streamChat(
      [{ role: "user", content: "hi" }], "auto", () => {},
      undefined, undefined, { cost_sensitivity: "low" },
    )

    const init = fetchMock.mock.calls[0][1] as RequestInit
    const payload = JSON.parse(init.body as string)
    expect(payload.cost_sensitivity).toBe("low")
    expect(payload.model).toBe("auto")
  })
})

describe("ingestFeedback", () => {
  it("treats the 202 queued ack as success (fire-and-forget contract)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ status: "queued", job_id: "job-fb-1" }),
    })
    vi.stubGlobal("fetch", fetchMock)

    await expect(
      ingestFeedback("hi", "hello there", "test-model", "convo-1"),
    ).resolves.toBeUndefined()

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain("/ingest/feedback")
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.user_message).toBe("hi")
    expect(body.conversation_id).toBe("convo-1")
  })

  it("rejects on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    }))

    await expect(
      ingestFeedback("hi", "hello", "test-model", "convo-1"),
    ).rejects.toThrow()
  })
})
