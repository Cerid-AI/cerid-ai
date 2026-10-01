// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { applySetupConfiguration } from "@/lib/api/setup"

beforeEach(() => {
  vi.restoreAllMocks()
})

function stubConfigure() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ success: true }),
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

describe("applySetupConfiguration", () => {
  it("sends the chosen inference backend", async () => {
    const fetchMock = stubConfigure()
    await applySetupConfiguration({
      keys: {},
      ollama_enabled: true,
      ollama_model: "gemma-4-26b-a4b",
      inference_backend: "ollama",
    })
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.inference_backend).toBe("ollama")
    expect(body.ollama_model).toBe("gemma-4-26b-a4b")
  })

  it("sends no backend when none was chosen", async () => {
    const fetchMock = stubConfigure()
    await applySetupConfiguration({ keys: {}, ollama_enabled: false })
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect("inference_backend" in body).toBe(false)
  })
})
