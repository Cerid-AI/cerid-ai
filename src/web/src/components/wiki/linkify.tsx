// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { Components } from "react-markdown"
import type { Element } from "hast"
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card"
import { Badge } from "@/components/ui/badge"

// ---------------------------------------------------------------------------
// Linkify — ReactMarkdown components prop that turns related-entity names
// into wikilinks inside summary prose.
//
// Rules (per spec):
//   - Match related_entities[].name only (no extra fetch required).
//   - Word-boundary match, case-insensitive.
//   - Longest-first to avoid short names clobbering longer ones.
//   - Link-once-per-paragraph (tracks seen names in a per-paragraph Set).
//   - Skip code spans and existing links (handled by scoping to plain text nodes).
//
// Three-state link semantics:
//   has_summary true   → normal link (brand color, underline on hover)
//   entity exists,     → stub: muted foreground + dashed underline
//   has_summary false    (non-color-only — dashed underline, aria-label)
//   (no match)         → plain text (anti-pattern: don't overlink)
//
// HoverCard previews:
//   has_summary true  → one_liner text + type badge
//   stub              → "Summary pending — written by the nightly refresh"
// ---------------------------------------------------------------------------

export interface LinkifyEntity {
  slug: string
  name: string
  entity_type: string
  has_summary: boolean
  one_liner: string | null
}

interface LinkifyOptions {
  entities: LinkifyEntity[]
  onSelect: (slug: string) => void
}

// Build a pattern that matches entity names as whole words, longest first.
function buildPattern(names: string[]): RegExp | null {
  if (names.length === 0) return null
  const sorted = [...names].sort((a, b) => b.length - a.length)
  const escaped = sorted.map((n) =>
    n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  )
  return new RegExp(`\\b(${escaped.join("|")})\\b`, "gi")
}

interface WikiLinkProps {
  entity: LinkifyEntity
  onSelect: (slug: string) => void
  displayText: string
}

function WikiLink({ entity, onSelect, displayText }: WikiLinkProps) {
  const isStub = !entity.has_summary

  const trigger = (
    <button
      type="button"
      onClick={() => onSelect(entity.slug)}
      aria-label={
        isStub
          ? `${displayText} — summary pending`
          : `Navigate to ${displayText}`
      }
      className={
        isStub
          ? "cursor-pointer text-muted-foreground underline decoration-dashed underline-offset-2 hover:text-foreground"
          : "cursor-pointer text-brand underline-offset-2 hover:underline"
      }
    >
      {displayText}
    </button>
  )

  return (
    <HoverCard openDelay={300} closeDelay={100}>
      <HoverCardTrigger asChild>{trigger}</HoverCardTrigger>
      <HoverCardContent className="w-64 space-y-1.5 p-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-foreground">{entity.name}</span>
          <Badge variant="outline" className="font-mono text-label-xxs uppercase">
            {entity.entity_type}
          </Badge>
        </div>
        <p className="text-muted-foreground">
          {isStub
            ? "Summary pending — written by the nightly refresh."
            : (entity.one_liner ?? entity.name)}
        </p>
      </HoverCardContent>
    </HoverCard>
  )
}

/**
 * Build the ReactMarkdown `components` prop that linkifies related-entity
 * names inside prose text nodes. Caller passes this directly as `components`
 * to `<ReactMarkdown>`.
 *
 * Only `p` paragraphs are processed (not headings, code, blockquotes etc.).
 * Each paragraph tracks its own "seen names" set so names only link once per paragraph.
 */
export function buildLinkifyComponents(options: LinkifyOptions): Components {
  const { entities, onSelect } = options

  // Sort longest first so "Python 3.14" matches before "Python".
  const sortedEntities = [...entities].sort((a, b) => b.name.length - a.name.length)
  const nameToEntity = new Map<string, LinkifyEntity>(
    sortedEntities.map((e) => [e.name.toLowerCase(), e]),
  )
  const pattern = buildPattern(sortedEntities.map((e) => e.name))

  function processText(
    text: string,
    seenThisParagraph: Set<string>,
    key: string,
  ): React.ReactNode[] {
    if (!pattern) return [text]

    const nodes: React.ReactNode[] = []
    let lastIndex = 0
    let match: RegExpExecArray | null

    // Reset lastIndex before iteration (shared RegExp object).
    pattern.lastIndex = 0

    while ((match = pattern.exec(text)) !== null) {
      const matched = match[1]
      const matchedLower = matched.toLowerCase()
      const entity = nameToEntity.get(matchedLower)
      if (!entity) continue

      // Link once per paragraph.
      if (seenThisParagraph.has(matchedLower)) continue
      seenThisParagraph.add(matchedLower)

      // Text before the match.
      if (match.index > lastIndex) {
        nodes.push(text.slice(lastIndex, match.index))
      }

      nodes.push(
        <WikiLink
          key={`${key}-${match.index}`}
          entity={entity}
          onSelect={onSelect}
          displayText={matched}
        />,
      )

      lastIndex = match.index + matched.length
    }

    if (lastIndex < text.length) {
      nodes.push(text.slice(lastIndex))
    }

    return nodes
  }

  // Process the hast children of a paragraph recursively, turning text nodes
  // into arrays of strings and WikiLink elements.
  function processChildren(
    children: React.ReactNode,
    seenThisParagraph: Set<string>,
    paragraphKey: string,
  ): React.ReactNode {
    if (typeof children === "string") {
      return processText(children, seenThisParagraph, paragraphKey)
    }
    if (Array.isArray(children)) {
      return children.flatMap((child, i) =>
        processChildren(child, seenThisParagraph, `${paragraphKey}-${i}`),
      )
    }
    return children
  }

  return {
    p({ node, children, ...props }) {
      // Build a stable key from the paragraph's position in the hast tree.
      const paragraphKey = String((node as Element | undefined)?.position?.start.offset ?? Math.random())
      const seenThisParagraph = new Set<string>()
      const processed = processChildren(children, seenThisParagraph, paragraphKey)
      return <p {...props}>{processed}</p>
    },
  }
}
