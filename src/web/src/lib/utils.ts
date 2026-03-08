// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { ChatMessage } from "@/lib/types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Generate a UUID v4, with fallback for insecure contexts (plain HTTP on LAN). */
export function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID()
  // Fallback: crypto.getRandomValues is available in all contexts
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 1
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/**
 * Estimate token counts from messages (chars / 4 heuristic).
 * Shared between ChatDashboard and useLiveMetrics.
 */
export function estimateTokens(messages: ChatMessage[]): { input: number; output: number } {
  let input = 0
  let output = 0
  for (const msg of messages) {
    const tokens = Math.ceil(msg.content.length / 4)
    if (msg.role === "assistant") {
      output += tokens
    } else {
      input += tokens
    }
  }
  return { input, output }
}

/**
 * Calculate USD cost from token count and per-1M price.
 */
export function tokenCost(tokens: number, costPer1M: number): number {
  return (tokens * costPer1M) / 1_000_000
}

/**
 * Format a dollar cost for display.
 * $0.00 for zero, <$0.01 for sub-cent, $X.XX otherwise (up to 4 decimal places for tiny costs).
 */
export function formatCost(dollars: number): string {
  if (dollars <= 0) return "$0.00"
  if (dollars < 0.01) return dollars < 0.001 ? `$${dollars.toFixed(4)}` : `$${dollars.toFixed(3)}`
  return `$${dollars.toFixed(2)}`
}

/**
 * Accuracy tier: text color, bar color, and label based on accuracy (0–1 range).
 * Thresholds: ≥0.8 = High (green), ≥0.5 = Medium (yellow), <0.5 = Low (red).
 */
export function getAccuracyTier(accuracy: number): {
  label: string
  textColor: string
  barColor: string
} {
  if (accuracy >= 0.8) return { label: "High", textColor: "text-green-400", barColor: "bg-green-500" }
  if (accuracy >= 0.5) return { label: "Medium", textColor: "text-yellow-400", barColor: "bg-yellow-500" }
  return { label: "Low", textColor: "text-red-400", barColor: "bg-red-500" }
}

/**
 * Determine render mode from a filename extension.
 */
const CODE_EXTS = new Set([
  "py", "js", "ts", "jsx", "tsx", "go", "rs", "rb", "java", "c", "cpp", "cs",
  "swift", "kt", "sh", "bash", "zsh", "sql", "css", "scss", "html", "xml",
  "json", "yaml", "yml", "toml", "dockerfile", "diff",
])
const TABLE_EXTS = new Set(["csv", "tsv", "xlsx"])
const MARKDOWN_EXTS = new Set(["md", "markdown", "mdx"])

export function getFileRenderMode(filename: string): "code" | "markdown" | "table" | "text" {
  const ext = filename.split(".").pop()?.toLowerCase() ?? ""
  if (CODE_EXTS.has(ext)) return "code"
  if (MARKDOWN_EXTS.has(ext)) return "markdown"
  if (TABLE_EXTS.has(ext)) return "table"
  return "text"
}

const EXT_TO_LANGUAGE: Record<string, string> = {
  py: "python", js: "javascript", ts: "typescript", jsx: "jsx", tsx: "tsx",
  go: "go", rs: "rust", rb: "ruby", java: "java", c: "c", cpp: "cpp",
  cs: "csharp", swift: "swift", kt: "kotlin", sh: "bash", bash: "bash",
  zsh: "bash", sql: "sql", css: "css", scss: "scss", html: "html",
  xml: "xml", json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
  dockerfile: "dockerfile", diff: "diff", md: "markdown",
}

/**
 * Map filename extension to PrismLight language name.
 */
export function getLanguageFromFilename(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? ""
  return EXT_TO_LANGUAGE[ext] ?? "text"
}

/**
 * Format a byte count for human display (B / KB / MB).
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Parse a value that may be a JSON-encoded string array, a native array, or undefined.
 * Returns a clean `string[]` regardless of input shape.
 */
export function parseTags(value: unknown): string[] {
  if (Array.isArray(value)) return value
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value)
      if (Array.isArray(parsed)) return parsed
    } catch { /* invalid JSON — fall through */ }
  }
  return []
}