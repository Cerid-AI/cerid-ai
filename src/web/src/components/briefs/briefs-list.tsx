// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * BriefsList — day-grouped list of brief cards (Task 2.2).
 *
 * Briefs arrive newest-first from the backend; grouping by
 * `toDateString()` into a Map preserves that ordering (first occurrence
 * of a day determines its position) with no date library required.
 */

import { CalendarDays } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { LastUpdated } from "@/components/ui/last-updated"
import type { Brief } from "@/lib/types/brief"

interface BriefsListProps {
  briefs: Brief[]
  onSelect: (id: string) => void
}

function groupByDay(briefs: Brief[]): Array<[string, Brief[]]> {
  const groups = new Map<string, Brief[]>()
  for (const brief of briefs) {
    const day = new Date(brief.generated_at).toDateString()
    const existing = groups.get(day)
    if (existing) existing.push(brief)
    else groups.set(day, [brief])
  }
  return Array.from(groups.entries())
}

/** Strips common markdown syntax for the plain-text card preview (detail view keeps full ReactMarkdown rendering). */
function stripMarkdownPreview(text: string, maxLen = 140): string {
  const stripped = text
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/[*_`]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
  return stripped.length > maxLen ? `${stripped.slice(0, maxLen).trimEnd()}…` : stripped
}

function previewText(brief: Brief): string {
  const first = brief.sections[0]
  if (!first) return "No summary available"
  return stripMarkdownPreview(first.body || first.title)
}

export function BriefsList({ briefs, onSelect }: BriefsListProps) {
  const groups = groupByDay(briefs)
  let staggerIndex = 0

  return (
    <div className="space-y-6">
      {groups.map(([day, dayBriefs]) => (
        <section key={day} aria-label={day}>
          <h2 className="mb-2 flex items-center gap-1.5 text-label-sm font-medium uppercase tracking-wide text-muted-foreground">
            <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
            {day}
          </h2>
          <div className="space-y-3">
            {dayBriefs.map((brief) => {
              const i = staggerIndex++
              return (
                <Card
                  key={brief.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(brief.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelect(brief.id)
                    }
                  }}
                  aria-label={`Open ${brief.kind} brief`}
                  style={{ ["--i" as string]: Math.min(i, 8) }} // drift-allowed: animation stagger index
                  className="cerid-stagger-fast cursor-pointer py-4 transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <CardHeader className="px-4 pb-2">
                    <div className="flex items-center justify-between gap-3">
                      <CardTitle className="text-base capitalize">{brief.kind} brief</CardTitle>
                      <LastUpdated timestamp={Date.parse(brief.generated_at)} />
                    </div>
                  </CardHeader>
                  <CardContent className="px-4 pt-0">
                    <p className="line-clamp-2 text-sm text-muted-foreground">
                      {previewText(brief)}
                    </p>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}
