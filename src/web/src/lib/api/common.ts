// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Runtime config (window.__ENV__ from docker-entrypoint.sh) takes precedence
// over build-time Vite env vars, enabling config changes without rebuild.
const _env = (globalThis as Record<string, unknown>).__ENV__ as Record<string, string> | undefined
const _rawMcpUrl = _env?.VITE_MCP_URL || import.meta.env.VITE_MCP_URL || "/api/mcp"

// Self-healing: if the configured MCP URL points to a non-localhost host:port
// and we're served from localhost (Docker nginx proxy), prefer /api/mcp.
// This handles stale env-config.js cached by the browser.
function _resolveBaseUrl(raw: string): string {
  if (typeof window === "undefined") return raw
  // If we're on localhost but MCP URL points elsewhere, use the nginx proxy
  const isLocalOrigin = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  const isDirectPort = /^https?:\/\/[\d.]+:\d+/.test(raw) && !raw.includes("localhost") && !raw.includes("127.0.0.1")
  if (isLocalOrigin && isDirectPort) {
    return "/api/mcp"
  }
  return raw
}

export const MCP_BASE = _resolveBaseUrl(_rawMcpUrl)
// API key read from runtime injection ONLY — never from build-time env (would be baked into bundle)
const API_KEY = _env?.VITE_CERID_API_KEY || ""

import { uuid } from "@/lib/utils"
import { logSwallowedError } from "@/lib/log-swallowed"

export function mcpHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  headers["X-Client-ID"] = "gui"
  if (API_KEY) headers["X-API-Key"] = API_KEY
  headers["X-Request-ID"] = uuid()
  // Add JWT Bearer token if authenticated (multi-user mode)
  try {
    const token = localStorage.getItem("cerid-access-token")
    if (token) headers["Authorization"] = `Bearer ${token}`
  } catch (err) { logSwallowedError(err, "localStorage.getItem", { key: "cerid-access-token" }) }
  return headers
}

export async function extractError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json()
    // CeridError format: { error_code, message, details }
    if (body.error_code && body.message) {
      return `[${body.error_code}] ${body.message}`
    }
    // Legacy format: { detail: "..." }
    return body.detail ?? fallback
  } catch {
    return fallback
  }
}

/** Parsed structured error with typed discriminators. */
export interface ParsedError {
  message: string
  error_code?: string
  details?: Record<string, unknown>
  isFeatureGate: boolean
  isCreditExhausted: boolean
  isRateLimit: boolean
}

/** Parse CeridError structured response — handles both new and legacy error formats. */
export async function parseStructuredError(res: Response, fallback: string): Promise<ParsedError> {
  try {
    const body = await res.json()
    const code: string = body.error_code ?? ""
    return {
      message: body.message ?? body.detail ?? fallback,
      error_code: code || undefined,
      details: body.details ?? undefined,
      isFeatureGate: code.startsWith("FEATURE_GATE_"),
      isCreditExhausted: code.startsWith("PROVIDER_CREDIT_"),
      isRateLimit: code.startsWith("PROVIDER_RATE_"),
    }
  } catch {
    return { message: fallback, isFeatureGate: false, isCreditExhausted: false, isRateLimit: false }
  }
}
