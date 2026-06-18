// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared sectioned entity list used by both the search palette (variant="palette")
 * and the wiki pane entity list (variant="wiki").
 *
 * Features:
 * - Section headers: domain icon + Title-cased label + count + hue chip
 * - Per-section cap with terminal "N more in {domain} →" overflow row (palette)
 * - Headerless when only one section (byte-identical to pre-backbone flat list, A7)
 * - cerid-stagger-fast + --i index preserved from both call sites
 * - axe-clean: role="listbox"/role="option" (palette); ul/li (wiki)
 */

import type { DomainSection } from "@/lib/graph/organize"
import { domainSlot } from "@/lib/graph/identity"
import { domainIcon } from "@/lib/graph/domain-icons"
import { EntityListItem } from "@/components/wiki/entity-list-item"
import { Star } from "lucide-react"

// Sentinel domain value for the pinned Best Matches section in the palette.
export const BEST_MATCHES_DOMAIN = "__best__"

// ---------------------------------------------------------------------------
// Section header chip
// ---------------------------------------------------------------------------

interface SectionHeaderProps {
  section: DomainSection
}

function SectionHeader({ section }: SectionHeaderProps) {
  // Best Matches sentinel: Star icon, no hue chip
  if (section.domain === BEST_MATCHES_DOMAIN) {
    return (
      <div
        role="presentation"
        className="flex items-center gap-1.5 px-3 pb-0.5 pt-2 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
      >
        <Star className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span>{section.label}</span>
        <span className="ml-auto font-normal tabular-nums">{section.count}</span>
      </div>
    )
  }

  const Icon = domainIcon(section.icon)
  const slot = section.domain !== null ? domainSlot(section.domain) : null

  return (
    <div
      role="presentation"
      className="flex items-center gap-1.5 px-3 pb-0.5 pt-2 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
    >
      {slot !== null ? (
        <span
          className="inline-block h-2 w-2 shrink-0 rounded-sm"
          // drift-allowed: runtime domain slot color — CSS var resolved at paint
          style={{ backgroundColor: `var(--color-domain-${slot})` }} // drift-allowed: runtime-derived domain slot token
          aria-hidden="true"
        />
      ) : (
        <span
          className="inline-block h-2 w-2 shrink-0 rounded-sm bg-muted-foreground/40"
          aria-hidden="true"
        />
      )}
      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span>{section.label}</span>
      <span className="ml-auto font-normal tabular-nums">{section.count}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overflow row (palette variant) — "N more in Research →"
// ---------------------------------------------------------------------------

interface OverflowRowProps {
  section: DomainSection
  onNavigateToDomain: (domain: string | null) => void
}

function OverflowRow({ section, onNavigateToDomain }: OverflowRowProps) {
  if (section.overflow === 0) return null
  return (
    <li role="none">
      <button
        type="button"
        onClick={() => onNavigateToDomain(section.domain)}
        className="flex w-full items-center gap-1 px-4 py-1.5 text-left text-label-xs text-muted-foreground transition-colors hover:bg-accent/30 hover:text-foreground"
        aria-label={`Show ${section.overflow} more in ${section.label}`}
      >
        <span>
          {section.overflow} more in {section.label}
        </span>
        <span aria-hidden="true">→</span>
      </button>
    </li>
  )
}

// ---------------------------------------------------------------------------
// SectionedEntityListPalette
// ---------------------------------------------------------------------------

export interface SectionedEntityListPaletteProps {
  variant: "palette"
  sections: DomainSection[]
  /**
   * Flat entity row index (headers excluded) that is currently highlighted.
   * Arrow keys advance this counter; the component renders aria-selected accordingly.
   */
  highlightIndex: number
  onHighlight: (flatIndex: number) => void
  onPick: (slug: string) => void
  /** Called when "N more in {domain} →" overflow row is clicked. */
  onNavigateToDomain: (domain: string | null) => void
  listboxId: string
  /** True when there is only one section — render headerless (no section dividers). */
  headerless: boolean
}

export function SectionedEntityListPalette({
  sections,
  highlightIndex,
  onHighlight,
  onPick,
  onNavigateToDomain,
  listboxId,
  headerless,
}: SectionedEntityListPaletteProps) {
  // Flat entity counter — unique index per entity row, shared across sections.
  // Matches the parent's highlightIndex state. Headers are skipped.
  let flatCounter = 0

  const allRows: React.ReactNode[] = []

  for (const section of sections) {
    // Section header: rendered as a <li role="none"> so it participates in the
    // listbox structure without being an interactive or option element.
    if (!headerless && sections.length > 1) {
      allRows.push(
        <li key={`hdr-${section.domain ?? "other"}`} role="none">
          <SectionHeader section={section} />
        </li>,
      )
    }

    // Entity rows: <li role="option"> is the interactive element in the combobox
    // pattern. The input carries focus via aria-activedescendant; options are
    // NOT independently focused (no tabIndex). No nested buttons.
    for (const entity of section.entities) {
      const fi = flatCounter++
      const isHighlighted = fi === highlightIndex
      allRows.push(
        <li
          key={entity.slug}
          id={`${listboxId}-option-${fi}`}
          role="option"
          aria-selected={isHighlighted}
          onClick={() => onPick(entity.slug)}
          onMouseEnter={() => onHighlight(fi)}
          // drift-allowed: stagger delay via CSS custom property (animation)
          style={{ ["--i" as string]: Math.min(fi, 8) }} // drift-allowed: animation stagger index
          className={`cerid-stagger-fast flex cursor-default items-center justify-between px-4 py-2 text-sm ${
            isHighlighted
              ? "bg-accent text-accent-foreground"
              : "text-foreground/85 hover:bg-accent/40"
          }`}
        >
          <span className="truncate">{entity.name}</span>
          <span className="ml-2 shrink-0 text-label-xs text-muted-foreground">
            {entity.slug}
          </span>
        </li>,
      )
    }

    // Overflow row (palette variant, non-headerless path)
    if (section.overflow > 0) {
      allRows.push(
        <OverflowRow
          key={`ovf-${section.domain ?? "other"}`}
          section={section}
          onNavigateToDomain={onNavigateToDomain}
        />,
      )
    }
  }

  return (
    <ul
      role="listbox"
      id={listboxId}
      aria-label="Search results"
      className="max-h-80 overflow-y-auto py-2"
    >
      {allRows}
    </ul>
  )
}

// ---------------------------------------------------------------------------
// SectionedEntityListWiki
// ---------------------------------------------------------------------------

export interface SectionedEntityListWikiProps {
  variant: "wiki"
  sections: DomainSection[]
  headerless: boolean
  selectedSlug: string | null
  onSelect: (slug: string) => void
}

export function SectionedEntityListWiki({
  sections,
  headerless,
  selectedSlug,
  onSelect,
}: SectionedEntityListWikiProps) {
  if (headerless) {
    // Single section: flat list, no headers — byte-identical to pre-backbone (A7)
    const section = sections[0]
    if (!section) return null
    return (
      <ul className="space-y-1 p-2" aria-label="Entity list">
        {section.entities.map((entity, i) => (
          <li
            key={entity.slug}
            // drift-allowed: stagger delay via CSS custom property (animation)
            style={{ ["--i" as string]: Math.min(i, 8) }} // drift-allowed: animation stagger index
            className="cerid-stagger-fast"
          >
            <EntityListItem
              entity={entity}
              selected={entity.slug === selectedSlug}
              onSelect={onSelect}
            />
          </li>
        ))}
      </ul>
    )
  }

  // Multi-section: each domain group is a <li> in the outer list, containing a
  // section header div and an inner <ul> of entity items. This avoids the axe
  // "list children must be listitem" violation caused by role="presentation" <li>
  // siblings in a flat list.
  let staggerBase = 0
  return (
    <ul className="space-y-1 p-2" aria-label="Entity list">
      {sections.map((section) => {
        const base = staggerBase
        staggerBase += section.entities.length
        return (
          <li key={section.domain ?? "__other__"} className="space-y-0.5">
            <SectionHeader section={section} />
            <ul>
              {section.entities.map((entity, i) => (
                <li
                  key={entity.slug}
                  // drift-allowed: stagger delay via CSS custom property (animation)
                  style={{ ["--i" as string]: Math.min(base + i, 8) }} // drift-allowed: animation stagger index
                  className="cerid-stagger-fast"
                >
                  <EntityListItem
                    entity={entity}
                    selected={entity.slug === selectedSlug}
                    onSelect={onSelect}
                  />
                </li>
              ))}
            </ul>
          </li>
        )
      })}
    </ul>
  )
}
