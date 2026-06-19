// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * KB context utilities — semantic dedup + domain headers for LLM injection.
 */

import type { KBQueryResult, MemoryRecallResult, ExternalSourceResult } from "./types"

// ---------------------------------------------------------------------------
// Context budget — mirrors backend MODEL_CONTEXT_CHAR_BUDGETS (settings.py)
// ---------------------------------------------------------------------------

/**
 * Per-model-family char budgets for client-side context assembly.
 *
 * Keys are lowercase family prefixes matched against the bare model name
 * (provider prefix stripped). Matching rule mirrors the backend:
 *   model.toLowerCase().split("/").at(-1)?.startsWith(prefix)
 *
 * Default (unknown family): 40 000 chars.
 */
export const MODEL_CONTEXT_CHAR_BUDGETS: Record<string, number> = {
  "claude":      120_000,  // Claude: 1M context
  "gemini":      120_000,  // Gemini: 1M context
  "gpt-4o-mini": 20_000,   // GPT-4o-mini: cheaper, tighter budget (checked before gpt-4o)
  "gpt-4o":      40_000,   // GPT-4o: 128K context
  "llama":       16_000,   // Llama: 32K–128K context
  "grok":        60_000,   // Grok: 2M context
}

const DEFAULT_CONTEXT_BUDGET = 40_000

/**
 * Resolve the char budget for a given model ID.
 *
 * Strips any provider prefix (e.g. "openrouter/anthropic/") before matching,
 * exactly mirroring the backend's split("/")[-1] approach.
 */
export function getContextBudget(modelId: string): number {
  const bare = modelId.toLowerCase().split("/").at(-1) ?? modelId.toLowerCase()
  for (const [prefix, budget] of Object.entries(MODEL_CONTEXT_CHAR_BUDGETS)) {
    if (bare.startsWith(prefix)) return budget
  }
  return DEFAULT_CONTEXT_BUDGET
}

/**
 * Select documents to include in LLM context without exceeding the per-model
 * char budget.
 *
 * Strategy:
 * - Sources must be pre-sorted by descending relevance (highest first).
 * - Whole documents are kept or dropped — never truncated mid-document.
 * - Iteration stops at the first document that would push total chars over
 *   the budget; subsequent documents are also dropped even if smaller.
 *
 * Returns an object with:
 *   - `selected`: the sources that fit within the budget
 *   - `dropped`: sources excluded due to budget exhaustion
 */
export function selectDocsWithinBudget(
  sources: KBQueryResult[],
  modelId: string,
): { selected: KBQueryResult[]; dropped: KBQueryResult[] } {
  const budget = getContextBudget(modelId)
  let usedChars = 0
  const selected: KBQueryResult[] = []
  const dropped: KBQueryResult[] = []
  let budgetExhausted = false

  for (const source of sources) {
    if (budgetExhausted) {
      dropped.push(source)
      continue
    }
    const docChars = source.content.length
    if (usedChars + docChars > budget) {
      budgetExhausted = true
      dropped.push(source)
    } else {
      usedChars += docChars
      selected.push(source)
    }
  }

  return { selected, dropped }
}

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
 *
 * Phase 1.2: when present on the source object, also emits:
 *   type="<source_type>"   — source class (kb/pack/memory/wiki/external)
 *   date="YYYY-MM-DD"      — derived from created_at; omitted when null/absent
 */
export function formatChunkWithHeader(source: KBQueryResult): string {
  const attrs: string[] = []
  if (source.artifact_id) attrs.push(`id="${source.artifact_id}"`)
  if (source.domain) attrs.push(`domain="${source.domain}"`)
  if (source.sub_category) attrs.push(`category="${source.sub_category}"`)
  if (source.filename) attrs.push(`source="${source.filename}"`)
  if (source.chunk_index != null) attrs.push(`chunk="${source.chunk_index}"`)
  if (source.relevance != null) attrs.push(`relevance="${source.relevance.toFixed(2)}"`)
  // Phase 1.2 additive attributes — omit when absent (defensive; older cached payloads
  // may not carry these fields).
  if (source.source_type) attrs.push(`type="${source.source_type}"`)
  const dateStr = extractDate(source.created_at)
  if (dateStr) attrs.push(`date="${dateStr}"`)
  const attrStr = attrs.length > 0 ? " " + attrs.join(" ") : ""
  return `<document${attrStr}>\n${source.content}\n</document>`
}

/**
 * Extract the YYYY-MM-DD date portion from an ISO date/datetime string.
 * Returns null when the input is null, undefined, empty, or unparseable.
 */
function extractDate(value: string | null | undefined): string | null {
  if (!value) return null
  // ISO strings are "YYYY-MM-DDThh:mm:ssZ" or "YYYY-MM-DD" — the date is always
  // the first 10 characters when the string is long enough and follows the format.
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/)
  return match ? match[1] : null
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

/**
 * Convert an ExternalSourceResult (Wikipedia, DuckDuckGo, etc.) to a
 * KBQueryResult shape so it can flow through the shared dedup / auto-inject /
 * source-attribution pipelines alongside KB chunks. Preserves the origin URL
 * and display name so the sources pane can render a link back to the source.
 */
export function externalToKBResult(ext: ExternalSourceResult): KBQueryResult {
  const displayName = ext.source_name || safeHostname(ext.source_url) || "External"
  return {
    content: ext.content,
    relevance: ext.relevance,
    // Use the URL as a stable artifact_id so dedupeByArtifact can group
    // multiple chunks from the same external page.
    artifact_id: ext.source_url,
    filename: displayName,
    domain: "external",
    chunk_index: 0,
    collection: "external",
    ingested_at: "",
    source_type: "external",
    source_url: ext.source_url,
    source_name: ext.source_name,
  }
}

function safeHostname(url: string): string | null {
  try {
    return new URL(url).hostname
  } catch {
    return null
  }
}

/**
 * Return `url` only if it is a safe http(s) link, else `null`. Guards against
 * `javascript:` / `data:` / `vbscript:` hrefs — a spoofed adapter could put
 * one in an external-reference field and turn it into one-click XSS. Use the
 * result as `href={safeHttpUrl(u) ?? undefined}` so unsafe links render inert.
 */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const protocol = new URL(url, window.location.origin).protocol
    return protocol === "http:" || protocol === "https:" ? url : null
  } catch {
    return null
  }
}
