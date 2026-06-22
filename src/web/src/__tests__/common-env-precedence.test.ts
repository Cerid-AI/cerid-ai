// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// MCP_BASE / API key resolution precedence: window.cerid.env (desktop remote
// mode) > window.__ENV__ (docker runtime) > import.meta.env > "/api/mcp".
// common.ts reads config at module load, so each case resets modules + globals.

import { describe, it, expect, vi, afterEach } from "vitest"

afterEach(() => {
  delete (globalThis as Record<string, unknown>).cerid
  delete (globalThis as Record<string, unknown>).__ENV__
  vi.unstubAllEnvs()
  vi.resetModules()
})

async function loadCommon() {
  vi.resetModules()
  return import("@/lib/api/common")
}

describe("MCP_BASE precedence", () => {
  it("falls back to the nginx proxy when nothing is injected", async () => {
    vi.stubEnv("VITE_MCP_URL", "")
    const { MCP_BASE } = await loadCommon()
    expect(MCP_BASE).toBe("/api/mcp")
  })

  it("uses window.__ENV__ when present", async () => {
    // Non-numeric host so the localhost self-heal (which rewrites IP:port URLs
    // served from a localhost origin to /api/mcp) does not apply here.
    ;(globalThis as Record<string, unknown>).__ENV__ = { VITE_MCP_URL: "http://macpro.example:8888" }
    const { MCP_BASE } = await loadCommon()
    expect(MCP_BASE).toBe("http://macpro.example:8888")
  })

  it("window.cerid.env (desktop) overrides window.__ENV__", async () => {
    ;(globalThis as Record<string, unknown>).__ENV__ = { VITE_MCP_URL: "http://docker:8888" }
    ;(globalThis as Record<string, unknown>).cerid = {
      env: { VITE_MCP_URL: "https://macpro.local", VITE_CERID_API_KEY: "lan-key" }, // pragma: allowlist secret
    }
    const { MCP_BASE, mcpHeaders } = await loadCommon()
    expect(MCP_BASE).toBe("https://macpro.local")
    // The injected API key flows into request headers.
    expect(mcpHeaders()["X-API-Key"]).toBe("lan-key")
  })

  it("preserves an absolute remote URL under a non-localhost origin (Electron file://)", async () => {
    // jsdom default origin is http://localhost — emulate file:// (empty hostname)
    // so the localhost self-heal does NOT rewrite the remote URL to /api/mcp.
    const spy = vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      hostname: "",
      origin: "file://",
    } as Location)
    ;(globalThis as Record<string, unknown>).cerid = { env: { VITE_MCP_URL: "http://192.168.1.5:8888" } }
    const { MCP_BASE } = await loadCommon()
    expect(MCP_BASE).toBe("http://192.168.1.5:8888")
    spy.mockRestore()
  })
})
