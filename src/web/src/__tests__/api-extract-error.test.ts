// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * extractError() must produce a human-readable string for every error
 * shape the backend can return — most importantly the FastAPI 422 array.
 *
 * The 2026-04-23 incident: extractError returned `body.detail` raw, and
 * for FastAPI validation errors `detail` is an array of `{loc, msg, type}`
 * records. Calling `new Error([...])` stringifies it as "[object Object]"
 * — the user saw that in a yellow toast instead of the actual problem.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
const { extractError } = await import("@/lib/api/common")

function mockResponse(body: unknown, status = 422): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response
}

describe("extractError", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("formats FastAPI 422 detail array as 'field: msg' pairs", async () => {
    const body = {
      detail: [
        { type: "missing", loc: ["body", "name"], msg: "Field required" },
        { type: "value_error", loc: ["body", "auto_inject_threshold"],
          msg: "Input should be greater than or equal to 0.5" },
      ],
    }
    const out = await extractError(mockResponse(body, 422), "fallback")
    // Must NOT be the raw object stringified
    expect(out).not.toBe("[object Object]")
    expect(out).not.toContain("[object Object]")
    // Should mention each field name + its message
    expect(out).toContain("name")
    expect(out).toContain("Field required")
    expect(out).toContain("auto_inject_threshold")
    expect(out).toContain("greater than or equal to 0.5")
  })

  it("returns CeridError shape as '[code] message'", async () => {
    const body = { error_code: "FEATURE_GATED", message: "Pro tier required" }
    const out = await extractError(mockResponse(body, 403), "fallback")
    expect(out).toBe("[FEATURE_GATED] Pro tier required")
  })

  it("returns legacy {detail: string} as the string", async () => {
    const body = { detail: "Server '%s' not found" }
    const out = await extractError(mockResponse(body, 404), "fallback")
    expect(out).toBe("Server '%s' not found")
  })

  it("returns the fallback when body is not JSON", async () => {
    const fakeResponse = {
      ok: false,
      status: 500,
      json: () => Promise.reject(new SyntaxError("Unexpected token")),
      text: () => Promise.resolve("Internal Server Error"),
    } as unknown as Response
    const out = await extractError(fakeResponse, "fallback message")
    expect(out).toBe("fallback message")
  })

  it("returns the fallback when body has neither error_code nor detail", async () => {
    const out = await extractError(mockResponse({ status: "x" }, 400), "fallback message")
    expect(out).toBe("fallback message")
  })

  it("returns the fallback when 422 detail array is empty", async () => {
    const out = await extractError(mockResponse({ detail: [] }, 422), "fallback message")
    expect(out).toBe("fallback message")
  })
})
