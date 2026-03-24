// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared verification display utilities.
 *
 * Maps backend verification status + method into user-facing display labels.
 * Key distinction: "unverified" from the backend can mean two different things:
 * - **Refuted**: Cross-model or web-search actively found the claim to be wrong.
 * - **Unverified**: KB simply has no matching evidence (softer, not a failure).
 *
 * This mapping is frontend-only — zero backend changes needed.
 */

export type ClaimDisplayStatus = "verified" | "refuted" | "unverified" | "uncertain" | "pending" | "evasion" | "citation" | "skipped"

export interface ClaimSpan {
  start: number
  end: number
  claim: string
  displayStatus: ClaimDisplayStatus
}

interface ClaimLike {
  claim: string
  status: string
  claim_type?: string
  verification_method?: string
}

/** Normalize whitespace: collapse runs of whitespace into single spaces, trim. */
function normalizeWS(s: string): string {
  return s.replace(/\s+/g, " ").trim()
}

/** Escape special regex characters in a string. */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/**
 * Match verification claims to positions in the response text.
 * Returns sorted, non-overlapping ClaimSpan[].
 *
 * When `domTextContent` is provided, positions are in the RAW textContent
 * coordinate system (matching the DOM TreeWalker), NOT normalized.
 *
 * Matching tiers (tried in order):
 * 1. Exact case-insensitive substring
 * 2. Whitespace-flexible regex (words joined by \s+)
 * 3. Whitespace-optional regex (words joined by \s* — handles DOM textContent
 *    missing spaces between block elements like <p>…</p><p>…</p>)
 * 4. First 5 significant words (>2 chars) with flexible whitespace
 * 5. Longest common contiguous word sequence (≥4 words)
 */
export function matchClaimsToText(text: string, claims: ClaimLike[], domTextContent?: string): ClaimSpan[] {
  if ((!text && !domTextContent) || !claims || claims.length === 0) return []

  // Use raw DOM text when available — positions must match DOM walker coordinates.
  // Do NOT normalize: the DOM walker uses raw textContent offsets.
  const searchText = domTextContent ?? normalizeWS(text)
  const searchLower = searchText.toLowerCase()
  const rawSpans: ClaimSpan[] = []

  for (const c of claims) {
    // Strip markdown formatting from claim since DOM text has no markdown
    const claimText = stripMarkdown(c.claim).trim()
    if (!claimText) continue

    const displayStatus = getClaimDisplayStatus(c.status, c.verification_method, c.claim_type)
    const claimLower = claimText.toLowerCase()

    // Tier 1: exact substring match (case-insensitive)
    const idx = searchLower.indexOf(claimLower)
    if (idx >= 0) {
      rawSpans.push({ start: idx, end: idx + claimLower.length, claim: c.claim, displayStatus })
      continue
    }

    const words = claimLower.split(/\s+/).filter((w) => w.length > 0)

    // Tier 2: whitespace-flexible match: words separated by \s+ regex
    if (words.length >= 2) {
      const flexPattern = words.map(escapeRegex).join("\\s+")
      try {
        const re = new RegExp(flexPattern, "i")
        const match = re.exec(searchText)
        if (match) {
          rawSpans.push({ start: match.index, end: match.index + match[0].length, claim: c.claim, displayStatus })
          continue
        }
      } catch { /* invalid regex, skip */ }
    }

    // Tier 3: whitespace-optional match — handles DOM textContent missing
    // spaces between block elements (e.g., "paragraph.Next paragraph")
    if (words.length >= 2) {
      const optPattern = words.map(escapeRegex).join("\\s*")
      try {
        const re = new RegExp(optPattern, "i")
        const match = re.exec(searchText)
        if (match) {
          rawSpans.push({ start: match.index, end: match.index + match[0].length, claim: c.claim, displayStatus })
          continue
        }
      } catch { /* invalid regex, skip */ }
    }

    // Tier 4: first 5 significant words (>2 chars) with flexible whitespace
    const sigWords = words.filter((w) => w.length > 2).slice(0, 5)
    if (sigWords.length >= 3) {
      const prefixPattern = sigWords.map(escapeRegex).join("\\s+")
      try {
        const re = new RegExp(prefixPattern, "i")
        const match = re.exec(searchText)
        if (match) {
          rawSpans.push({ start: match.index, end: match.index + match[0].length, claim: c.claim, displayStatus })
          continue
        }
      } catch { /* skip */ }
    }

    // Tier 5: longest contiguous word sequence (≥4 words) — handles LLM
    // paraphrasing by finding the longest verbatim subsequence
    if (words.length >= 4) {
      let bestMatch: { start: number; end: number } | null = null
      let bestLen = 0
      for (let wStart = 0; wStart <= words.length - 4; wStart++) {
        for (let wEnd = words.length; wEnd >= wStart + 4; wEnd--) {
          const subPattern = words.slice(wStart, wEnd).map(escapeRegex).join("\\s+")
          try {
            const re = new RegExp(subPattern, "i")
            const match = re.exec(searchText)
            if (match && match[0].length > bestLen) {
              bestMatch = { start: match.index, end: match.index + match[0].length }
              bestLen = match[0].length
              break // Found longest from this wStart
            }
          } catch { /* skip */ }
        }
        if (bestMatch) break // Use first (leftmost) best match
      }
      if (bestMatch) {
        rawSpans.push({ start: bestMatch.start, end: bestMatch.end, claim: c.claim, displayStatus })
      }
    }
  }

  // Sort by start position, then by length (longer first)
  rawSpans.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start))

  // De-overlap: keep first span when overlapping
  const result: ClaimSpan[] = []
  let lastEnd = -1
  for (const span of rawSpans) {
    if (span.start >= lastEnd) {
      result.push(span)
      lastEnd = span.end
    }
  }

  return result
}

/**
 * Derive the user-facing display status from backend status + verification method.
 *
 * - evasion claim_type → evasion (orange, model deflected)
 * - citation claim_type → citation (purple, source verification)
 * - verified → verified (green)
 * - unverified + cross_model/web_search → refuted (red, actively wrong)
 * - unverified + kb/none → unverified (yellow, no evidence)
 * - uncertain → uncertain (gray, checked but inconclusive)
 * - pending → pending (gray, spinning)
 */
export function getClaimDisplayStatus(
  status: string,
  verificationMethod?: string,
  claimType?: string,
): ClaimDisplayStatus {
  // Evasion claims get special orange treatment regardless of verification outcome
  if (claimType === "evasion") return "evasion"
  // Citation verification gets purple treatment
  if (claimType === "citation") return "citation"
  if (status === "verified") return "verified"
  if (
    status === "unverified" &&
    (verificationMethod === "cross_model" || verificationMethod === "web_search")
  )
    return "refuted" // actively found wrong by another model
  if (status === "unverified") return "unverified" // no KB evidence (softer)
  if (status === "skipped") return "skipped"
  if (status === "uncertain") return "uncertain"
  if (status === "pending") return "pending"
  return "uncertain"
}

/** Shared display-status → color classes map used across claim UI components.
 * Uses light/dark-adaptive Tailwind colors for WCAG AA compliance:
 * - Light mode: -700 text on -50 bg (contrast ratio ~7:1)
 * - Dark mode: -400 text on -500/20 bg (contrast ratio ~5:1)
 */
export const DISPLAY_STATUS_COLORS: Record<ClaimDisplayStatus | "error", string> = {
  verified: "bg-green-50 text-green-700 border-green-200 dark:bg-green-500/20 dark:text-green-400 dark:border-green-500/30",
  refuted: "bg-red-50 text-red-700 border-red-200 dark:bg-red-500/20 dark:text-red-400 dark:border-red-500/30",
  unverified: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-yellow-500/20 dark:text-yellow-400 dark:border-yellow-500/30",
  evasion: "bg-orange-50 text-orange-700 border-orange-200 dark:bg-orange-500/20 dark:text-orange-400 dark:border-orange-500/30",
  citation: "bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-500/20 dark:text-purple-400 dark:border-purple-500/30",
  skipped: "bg-muted/50 text-muted-foreground border-border/50",
  uncertain: "bg-muted/50 text-muted-foreground border-border",
  pending: "bg-muted text-muted-foreground border-border",
  error: "bg-muted text-muted-foreground",
}

/** Verification method → human-readable label. */
export function verificationMethodLabel(method?: string): string | null {
  if (!method) return null
  if (method === "kb") return "kb"
  if (method === "cross_model") return "cross-model"
  if (method === "web_search") return "web search"
  if (method === "cross_model_failed") return "cross-model (failed)"
  if (method === "web_search_failed") return "web search (failed)"
  return method
}

/** Verification method → badge color classes (light/dark adaptive). */
export function verificationMethodColor(method?: string): string {
  if (method === "cross_model") return "bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-500/15 dark:text-purple-400 dark:border-purple-500/30"
  if (method === "web_search") return "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/15 dark:text-blue-400 dark:border-blue-500/30"
  if (method === "kb") return "bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-500/15 dark:text-cyan-400 dark:border-cyan-500/30"
  return "bg-muted text-muted-foreground border-border"
}

/**
 * Strip common Markdown formatting from claim text for clean display.
 * Handles bold, italic, inline code, links, headers, and list markers.
 */
export function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")       // **bold**
    .replace(/__(.+?)__/g, "$1")            // __bold__
    .replace(/\*(.+?)\*/g, "$1")            // *italic*
    .replace(/_(.+?)_/g, "$1")              // _italic_
    .replace(/`([^`]+)`/g, "$1")            // `inline code`
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // [text](url)
    .replace(/^#{1,6}\s+/gm, "")            // # headers
    .replace(/^[-*+]\s+/gm, "")             // - list items
    .replace(/^\d+\.\s+/gm, "")             // 1. ordered list
    .replace(/^>\s+/gm, "")                 // > blockquote
    .trim()
}
