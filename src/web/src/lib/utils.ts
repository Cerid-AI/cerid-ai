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