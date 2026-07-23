// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import {
  jaccardSimilarity,
  deduplicateChunks,
  formatChunkWithHeader,
  getContextBudget,
  selectDocsWithinBudget,
  MODEL_CONTEXT_CHAR_BUDGETS,
} from "@/lib/kb-utils"
import type { KBQueryResult } from "@/lib/types"

// ---------------------------------------------------------------------------
// jaccardSimilarity
// ---------------------------------------------------------------------------

describe("jaccardSimilarity", () => {
  it("returns 1 for identical texts", () => {
    expect(jaccardSimilarity("hello world", "hello world")).toBe(1)
  })

  it("returns 0 for completely different texts", () => {
    expect(jaccardSimilarity("hello world", "foo bar baz")).toBe(0)
  })

  it("returns 1 for two empty strings", () => {
    expect(jaccardSimilarity("", "")).toBe(1)
  })

  it("returns 0 when one string is empty", () => {
    expect(jaccardSimilarity("hello", "")).toBe(0)
    expect(jaccardSimilarity("", "world")).toBe(0)
  })

  it("returns partial overlap correctly", () => {
    // "hello world foo" vs "hello world bar" → intersection {hello, world} = 2, union = 4
    expect(jaccardSimilarity("hello world foo", "hello world bar")).toBeCloseTo(0.5)
  })

  it("is case-insensitive", () => {
    expect(jaccardSimilarity("Hello World", "hello world")).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// deduplicateChunks
// ---------------------------------------------------------------------------

function makeKBResult(content: string, id = "art-1"): KBQueryResult {
  return {
    artifact_id: id,
    filename: "test.txt",
    domain: "test",
    sub_category: "general",
    content,
    relevance: 0.9,
    chunk_index: 0,
    collection: "domain_test",
    ingested_at: "2026-01-01T00:00:00Z",
    tags: [],
    quality_score: 1,
  }
}

describe("deduplicateChunks", () => {
  it("removes near-duplicate chunks", () => {
    const sources = [
      makeKBResult("the quick brown fox jumps over the lazy dog", "a1"),
      makeKBResult("the quick brown fox jumps over the lazy cat", "a2"), // 1 word different / 9 words = ~89% overlap
    ]
    const result = deduplicateChunks(sources, 0.7)
    expect(result).toHaveLength(1)
    expect(result[0].artifact_id).toBe("a1")
  })

  it("keeps distinct chunks", () => {
    const sources = [
      makeKBResult("python programming language features", "a1"),
      makeKBResult("javascript web development frameworks", "a2"),
    ]
    const result = deduplicateChunks(sources, 0.7)
    expect(result).toHaveLength(2)
  })

  it("returns empty for empty input", () => {
    expect(deduplicateChunks([])).toHaveLength(0)
  })

  it("respects threshold parameter", () => {
    const sources = [
      makeKBResult("hello world foo bar baz", "a1"),
      makeKBResult("hello world foo qux quux", "a2"), // 3/7 overlap = 0.43
    ]
    // With threshold 0.7 — should keep both
    expect(deduplicateChunks(sources, 0.7)).toHaveLength(2)
    // With threshold 0.3 — should dedup
    expect(deduplicateChunks(sources, 0.3)).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// formatChunkWithHeader
// ---------------------------------------------------------------------------

describe("formatChunkWithHeader", () => {
  it("wraps content in XML document tags with attributes", () => {
    const source = makeKBResult("some content")
    source.domain = "code"
    source.sub_category = "python"
    const result = formatChunkWithHeader(source)
    expect(result).toContain("<document")
    expect(result).toContain('domain="code"')
    expect(result).toContain('category="python"')
    expect(result).toContain('source="test.txt"')
    expect(result).toContain("some content")
    expect(result).toContain("</document>")
  })

  it("handles missing domain gracefully", () => {
    const source = makeKBResult("content here")
    source.domain = ""
    source.sub_category = ""
    const result = formatChunkWithHeader(source)
    expect(result).toContain("<document")
    expect(result).toContain('source="test.txt"')
    expect(result).toContain("content here")
  })

  it("handles domain without sub_category", () => {
    const source = makeKBResult("content")
    source.domain = "finance"
    source.sub_category = ""
    const result = formatChunkWithHeader(source)
    expect(result).toContain('domain="finance"')
    expect(result).not.toContain('category=')
  })

  // Phase 1.2: type= and date= attributes

  it("emits type attribute when source_type is present", () => {
    const source = makeKBResult("content")
    source.source_type = "kb"
    const result = formatChunkWithHeader(source)
    expect(result).toContain('type="kb"')
  })

  it("emits type attribute for pack source", () => {
    const source = makeKBResult("content")
    source.source_type = "pack"
    const result = formatChunkWithHeader(source)
    expect(result).toContain('type="pack"')
  })

  it("omits type attribute when source_type is absent", () => {
    const source = makeKBResult("content")
    delete (source as Partial<KBQueryResult>).source_type
    const result = formatChunkWithHeader(source)
    expect(result).not.toContain("type=")
  })

  it("emits date attribute as YYYY-MM-DD from ISO datetime string", () => {
    const source = makeKBResult("content")
    source.created_at = "2025-09-15T14:22:00Z"
    const result = formatChunkWithHeader(source)
    expect(result).toContain('date="2025-09-15"')
  })

  it("emits date attribute from bare date string", () => {
    const source = makeKBResult("content")
    source.created_at = "2026-01-01"
    const result = formatChunkWithHeader(source)
    expect(result).toContain('date="2026-01-01"')
  })

  it("omits date attribute when created_at is null", () => {
    const source = makeKBResult("content")
    source.created_at = null
    const result = formatChunkWithHeader(source)
    expect(result).not.toContain("date=")
  })

  it("omits date attribute when created_at is absent", () => {
    const source = makeKBResult("content")
    // created_at not set on the object at all
    const result = formatChunkWithHeader(source)
    expect(result).not.toContain("date=")
  })

  it("renders both type and date when both fields are present", () => {
    const source = makeKBResult("important content")
    source.source_type = "kb"
    source.created_at = "2026-03-20T08:00:00Z"
    const result = formatChunkWithHeader(source)
    expect(result).toContain('type="kb"')
    expect(result).toContain('date="2026-03-20"')
    // Verify full example attribute order: id domain category source chunk relevance type date
    expect(result).toMatch(/<document id="art-1".*type="kb".*date="2026-03-20"/)
  })
})

// ---------------------------------------------------------------------------
// getContextBudget
// ---------------------------------------------------------------------------

describe("getContextBudget", () => {
  it("returns claude budget for openrouter/anthropic/claude-sonnet-4.6", () => {
    expect(getContextBudget("openrouter/anthropic/claude-sonnet-4.6")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["claude"])
  })

  it("returns gemini budget for openrouter/google/gemini-3-flash-preview", () => {
    expect(getContextBudget("openrouter/google/gemini-3-flash-preview")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["gemini"])
  })

  it("returns gpt-4o-mini budget (not gpt-4o) for openrouter/openai/gpt-4o-mini", () => {
    expect(getContextBudget("openrouter/openai/gpt-4o-mini")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o-mini"])
    expect(getContextBudget("openrouter/openai/gpt-4o-mini")).toBe(20_000)
  })

  it("returns gpt-4o budget for openrouter/openai/gpt-4o", () => {
    expect(getContextBudget("openrouter/openai/gpt-4o")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["gpt-4o"])
    expect(getContextBudget("openrouter/openai/gpt-4o")).toBe(40_000)
  })

  it("returns llama budget for llama-3.3-70b-instruct", () => {
    expect(getContextBudget("openrouter/meta-llama/llama-3.3-70b-instruct")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["llama"])
  })

  it("returns grok budget for grok model", () => {
    expect(getContextBudget("openrouter/x-ai/grok-4.5")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["grok"])
  })

  it("returns default 40000 for unknown model", () => {
    expect(getContextBudget("unknown/model/name")).toBe(40_000)
  })

  it("is case-insensitive", () => {
    expect(getContextBudget("openrouter/anthropic/Claude-Sonnet-4.6")).toBe(MODEL_CONTEXT_CHAR_BUDGETS["claude"])
  })
})

// ---------------------------------------------------------------------------
// selectDocsWithinBudget
// ---------------------------------------------------------------------------

function makeDoc(id: string, chars: number, relevance = 0.9): KBQueryResult {
  return {
    artifact_id: id,
    filename: `${id}.txt`,
    domain: "test",
    content: "x".repeat(chars),
    relevance,
    chunk_index: 0,
    collection: "domain_test",
    ingested_at: "2026-01-01T00:00:00Z",
  }
}

describe("selectDocsWithinBudget", () => {
  it("keeps all docs that fit within the budget", () => {
    // Claude budget = 120_000 chars; two 10k docs fit easily
    const sources = [makeDoc("a", 10_000, 0.9), makeDoc("b", 10_000, 0.8)]
    const { selected, dropped } = selectDocsWithinBudget(sources, "openrouter/anthropic/claude-sonnet-4.6")
    expect(selected).toHaveLength(2)
    expect(dropped).toHaveLength(0)
  })

  it("drops whole documents once budget is exhausted", () => {
    // GPT-4o-mini budget = 20_000 chars
    const sources = [
      makeDoc("a", 15_000, 0.95),  // fits — 15k used
      makeDoc("b", 8_000, 0.90),   // 15k + 8k = 23k > 20k → dropped
      makeDoc("c", 2_000, 0.85),   // also dropped (budget already exhausted)
    ]
    const { selected, dropped } = selectDocsWithinBudget(sources, "openrouter/openai/gpt-4o-mini")
    expect(selected).toHaveLength(1)
    expect(selected[0].artifact_id).toBe("a")
    expect(dropped).toHaveLength(2)
    expect(dropped[0].artifact_id).toBe("b")
    expect(dropped[1].artifact_id).toBe("c")
  })

  it("never truncates a document mid-content", () => {
    // Budget = 40_000; first doc is exactly 40_000, second doc is 1 char
    const sources = [makeDoc("a", 40_000, 0.9), makeDoc("b", 1, 0.8)]
    const { selected } = selectDocsWithinBudget(sources, "openrouter/openai/gpt-4o")
    // First doc fits exactly; second would push over by 1 → dropped
    expect(selected).toHaveLength(1)
    expect(selected[0].artifact_id).toBe("a")
  })

  it("preserves relevance order (highest first) in selected", () => {
    // Sources are pre-sorted descending relevance; budget fits only first two
    const sources = [
      makeDoc("a", 8_000, 0.95),
      makeDoc("b", 8_000, 0.90),
      makeDoc("c", 8_000, 0.85),
    ]
    // GPT-4o-mini budget 20k; a+b=16k, c would push to 24k → dropped
    const { selected } = selectDocsWithinBudget(sources, "openrouter/openai/gpt-4o-mini")
    expect(selected.map((s) => s.artifact_id)).toEqual(["a", "b"])
  })

  it("returns all docs selected and none dropped for empty input", () => {
    const { selected, dropped } = selectDocsWithinBudget([], "openrouter/anthropic/claude-sonnet-4.6")
    expect(selected).toHaveLength(0)
    expect(dropped).toHaveLength(0)
  })

  it("uses default 40k budget for unknown model", () => {
    const sources = [
      makeDoc("a", 30_000, 0.9),
      makeDoc("b", 15_000, 0.8),  // 30k + 15k = 45k > 40k → dropped
    ]
    const { selected, dropped } = selectDocsWithinBudget(sources, "openrouter/unknown/model")
    expect(selected).toHaveLength(1)
    expect(selected[0].artifact_id).toBe("a")
    expect(dropped[0].artifact_id).toBe("b")
  })

  it("gpt-4o-mini gets 20k not gpt-4o's 40k", () => {
    // 25_000 chars would fit in the 40k gpt-4o budget but not the 20k mini budget
    const sources = [makeDoc("a", 25_000, 0.9)]
    const { selected: miniSelected } = selectDocsWithinBudget(sources, "openrouter/openai/gpt-4o-mini")
    const { selected: fullSelected } = selectDocsWithinBudget(sources, "openrouter/openai/gpt-4o")
    expect(miniSelected).toHaveLength(0)
    expect(fullSelected).toHaveLength(1)
  })
})
