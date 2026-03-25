// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { jaccardSimilarity, deduplicateChunks, formatChunkWithHeader } from "@/lib/kb-utils"
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
})
