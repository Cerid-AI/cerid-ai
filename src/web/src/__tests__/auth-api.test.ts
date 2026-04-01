// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "")

const {
  authRegister, authLogin, authRefresh, authLogout, authMe,
  authSetApiKey, authDeleteApiKey, authApiKeyStatus, authUsage,
} = await import("@/lib/api")

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch({}))
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

// ---------------------------------------------------------------------------
// Auth API functions
// ---------------------------------------------------------------------------

describe("authRegister", () => {
  it("sends POST to /auth/register", async () => {
    const tokenResponse = {
      access_token: "at",
      refresh_token: "rt",
      token_type: "bearer",
      expires_in: 900,
      user: { id: "u1", email: "test@example.com", role: "admin" },
    }
    vi.stubGlobal("fetch", mockFetch(tokenResponse))

    const result = await authRegister("test@example.com", "password123", "Test")
    expect(result.access_token).toBe("at")
    expect(result.user.email).toBe("test@example.com")

    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe("http://test-mcp:8888/auth/register")
    expect(opts.method).toBe("POST")
  })

  it("throws on 409 conflict", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Email already registered" }, 409))
    await expect(authRegister("dup@example.com", "pass1234")).rejects.toThrow("Email already registered")
  })
})

describe("authLogin", () => {
  it("sends POST to /auth/login", async () => {
    const tokenResponse = {
      access_token: "at",
      refresh_token: "rt",
      token_type: "bearer",
      expires_in: 900,
      user: { id: "u1", email: "test@example.com" },
    }
    vi.stubGlobal("fetch", mockFetch(tokenResponse))

    const result = await authLogin("test@example.com", "password123")
    expect(result.access_token).toBe("at")
  })

  it("throws on 401", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Invalid email or password" }, 401))
    await expect(authLogin("bad@example.com", "wrong")).rejects.toThrow("Invalid email or password")
  })
})

describe("authRefresh", () => {
  it("sends POST to /auth/refresh", async () => {
    vi.stubGlobal("fetch", mockFetch({ access_token: "new-at" }))

    const result = await authRefresh("my-refresh-token")
    expect(result.access_token).toBe("new-at")
  })
})

describe("authLogout", () => {
  it("sends POST to /auth/logout", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Logged out" }))
    await authLogout("my-refresh-token")

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe("http://test-mcp:8888/auth/logout")
  })
})

describe("authMe", () => {
  it("sends GET to /auth/me with Bearer token", async () => {
    localStorage.setItem("cerid-access-token", "my-token")
    vi.stubGlobal("fetch", mockFetch({ id: "u1", email: "test@example.com" }))

    const result = await authMe()
    expect(result.id).toBe("u1")

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(opts.headers.Authorization).toBe("Bearer my-token")
  })
})

describe("authSetApiKey", () => {
  it("sends PUT to /auth/me/api-key", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "API key saved" }))
    await authSetApiKey("sk-or-test123")

    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe("http://test-mcp:8888/auth/me/api-key")
    expect(opts.method).toBe("PUT")
  })
})

describe("authDeleteApiKey", () => {
  it("sends DELETE to /auth/me/api-key", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "API key removed" }))
    await authDeleteApiKey()

    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe("http://test-mcp:8888/auth/me/api-key")
    expect(opts.method).toBe("DELETE")
  })
})

describe("authApiKeyStatus", () => {
  it("returns has_key boolean", async () => {
    vi.stubGlobal("fetch", mockFetch({ has_key: true }))
    const result = await authApiKeyStatus()
    expect(result.has_key).toBe(true)
  })
})

describe("authUsage", () => {
  it("returns usage info", async () => {
    vi.stubGlobal("fetch", mockFetch({ queries: 42, ingestions: 7, month: "2026-03" }))
    const result = await authUsage()
    expect(result.queries).toBe(42)
    expect(result.ingestions).toBe(7)
    expect(result.month).toBe("2026-03")
  })
})

// ---------------------------------------------------------------------------
// Bearer token injection
// ---------------------------------------------------------------------------

describe("mcpHeaders Bearer token", () => {
  it("includes Authorization header when token is stored", async () => {
    localStorage.setItem("cerid-access-token", "test-jwt-token")
    vi.stubGlobal("fetch", mockFetch({ status: "ok" }))

    // Any API call should include the Bearer token
    const { fetchHealth } = await import("@/lib/api")
    await fetchHealth()

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(opts.headers.Authorization).toBe("Bearer test-jwt-token")
  })

  it("omits Authorization when no token stored", async () => {
    localStorage.removeItem("cerid-access-token")
    vi.stubGlobal("fetch", mockFetch({ status: "ok" }))

    const { fetchHealth } = await import("@/lib/api")
    await fetchHealth()

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(opts.headers.Authorization).toBeUndefined()
  })
})
