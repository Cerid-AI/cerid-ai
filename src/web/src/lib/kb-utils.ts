// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KB context utilities — semantic dedup + domain headers for LLM injection.
 */

import type { KBQueryResult, MemoryRecallResult } from "./types"

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
  sources: KBQueryResult[],
  threshold = 0.7,
): KBQueryResult[] {
  const kept: KBQueryResult[] = []
  for (const source of sources) {
    const isDuplicate = kept.some(
      (existing) => jaccardSimilarity(existing.content, source.content) >= threshold,
    )
    if (!isDuplicate) kept.push(source)
  }
  return kept
}

/**
 * Format a KB chunk with XML document tags for clear LLM boundary detection.
 *
 * XML tags provide:
 * - Clear document boundary delimiters (no ambiguity between chunks)
 * - Structured metadata attributes for source attribution
 * - Compatibility with smart routing's `<document` detection for injection counting
 */
export function formatChunkWithHeader(source: KBQueryResult): string {
  const attrs: string[] = []
  if (source.artifact_id) attrs.push(`id="${source.artifact_id}"`)
  if (source.domain) attrs.push(`domain="${source.domain}"`)
  if (source.sub_category) attrs.push(`category="${source.sub_category}"`)
  if (source.filename) attrs.push(`source="${source.filename}"`)
  if (source.chunk_index != null) attrs.push(`chunk="${source.chunk_index}"`)
  if (source.relevance != null) attrs.push(`relevance="${source.relevance.toFixed(2)}"`)
  const attrStr = attrs.length > 0 ? " " + attrs.join(" ") : ""
  return `<document${attrStr}>\n${source.content}\n</document>`
}

/**
 * Format a memory recall result for LLM injection.
 * Uses `<memory>` tags to distinguish from KB documents.
 */
export function formatMemoryForInjection(memory: MemoryRecallResult): string {
  const attrs: string[] = []
  if (memory.memory_type) attrs.push(`type="${memory.memory_type}"`)
  if (memory.relevance != null) attrs.push(`relevance="${memory.relevance.toFixed(2)}"`)
  if (memory.age_days != null) attrs.push(`age_days="${Math.round(memory.age_days)}"`)
  const attrStr = attrs.length > 0 ? " " + attrs.join(" ") : ""
  const label = memory.summary || memory.content
  return `<memory${attrStr}>\n${label}\n</memory>`
}

/**
 * Convert a MemoryRecallResult to a KBQueryResult shape so it can flow
 * through the existing dedup/injection pipeline alongside KB chunks.
 */
export function memoryToKBResult(memory: MemoryRecallResult): KBQueryResult {
  return {
    content: memory.summary || memory.content,
    relevance: memory.relevance,
    artifact_id: memory.memory_id,
    filename: `memory:${memory.memory_type}`,
    domain: "conversations",
    chunk_index: 0,
    collection: "memories",
    ingested_at: "",
    source_type: "memory",
  }
}
