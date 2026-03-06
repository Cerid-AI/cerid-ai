// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KB context utilities — semantic dedup + domain headers for LLM injection.
 */

import type { KBResult } from "./types"

/**
 * Word-set Jaccard similarity between two texts.
 * Returns 0–1 where 1 = identical word sets.
 */
export function jaccardSimilarity(a: string, b: string): number {
  const wordsA = new Set(a.toLowerCase().split(/\s+/).filter(Boolean))
  const wordsB = new Set(b.toLowerCase().split(/\s+/).filter(Boolean))
  if (wordsA.size === 0 && wordsB.size === 0) return 1
  if (wordsA.size === 0 || wordsB.size === 0) return 0

  let intersection = 0
  for (const w of wordsA) {
    if (wordsB.has(w)) intersection++
  }
  const union = wordsA.size + wordsB.size - intersection
  return union > 0 ? intersection / union : 0
}

/**
 * Remove KB chunks with high text overlap (Jaccard similarity above threshold).
 * Keeps the first occurrence (higher relevance, since results are pre-sorted).
 */
export function deduplicateChunks(
  sources: KBResult[],
  threshold = 0.7,
): KBResult[] {
  const kept: KBResult[] = []
  for (const source of sources) {
    const isDuplicate = kept.some(
      (existing) => jaccardSimilarity(existing.content, source.content) >= threshold,
    )
    if (!isDuplicate) kept.push(source)
  }
  return kept
}

/**
 * Format a KB chunk with a domain/filename header prefix for LLM context.
 */
export function formatChunkWithHeader(source: KBResult): string {
  const parts: string[] = []
  if (source.domain) parts.push(source.domain)
  if (source.sub_category) parts.push(source.sub_category)
  const domainPath = parts.length > 0 ? parts.join(" > ") + " | " : ""
  return `--- ${domainPath}${source.filename} ---\n${source.content}`
}
