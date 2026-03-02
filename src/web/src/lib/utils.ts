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