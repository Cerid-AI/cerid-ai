// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "")

const { uploadFile } = await import("@/lib/api")

beforeEach(() => vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
  ok: true, status: 200, json: () => Promise.resolve({ status: "success", artifact_id: "a1" }),
})))
afterEach(() => vi.restoreAllMocks())

function uploadUrl(): URL {
  return new URL(String(vi.mocked(fetch).mock.calls[0][0]))
}

describe("uploadFile", () => {
  it("names the chat a file was dropped in, so deleting the chat can offer it", async () => {
    await uploadFile(new File(["x"], "notes.pdf"), { domain: "general", conversationId: "conv-1" })
    expect(uploadUrl().pathname).toBe("/upload")
    expect(uploadUrl().searchParams.get("conversation_id")).toBe("conv-1")
  })

  it("names no chat for an upload from anywhere else", async () => {
    await uploadFile(new File(["x"], "notes.pdf"), { domain: "general" })
    expect(uploadUrl().searchParams.has("conversation_id")).toBe(false)
  })
})
