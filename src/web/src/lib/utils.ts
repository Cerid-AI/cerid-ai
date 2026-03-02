// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { ChatMessage } from "@/lib/types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
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