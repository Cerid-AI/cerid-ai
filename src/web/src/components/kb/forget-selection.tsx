// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useMemo } from "react"
import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { groupByDocument } from "@/hooks/use-forget-selection"
import type { ForgetSelection } from "@/hooks/use-forget-selection"
import type { ForgetSubject } from "@/lib/api"
import type { KBQueryResult } from "@/lib/types"

/** Select / Select all / Forget selected / Done. */
export function ForgetSelectionBar({ selection, all, onForget, className }: {
  selection: ForgetSelection
  all: ForgetSubject[]
  onForget: (subjects: ForgetSubject[]) => void
  className?: string
}) {
  if (!selection.active) {
    return (
      <Button
        variant="ghost"
        size="xs"
        className={className}
        disabled={all.length === 0}
        onClick={() => selection.setActive(true)}
      >
        Select
      </Button>
    )
  }
  const everything = all.length > 0 && selection.count === all.length
  return (
    <div className={`flex flex-wrap items-center gap-1 ${className ?? ""}`} role="toolbar" aria-label="Selection">
      <Button
        variant="ghost"
        size="xs"
        onClick={() => (everything ? selection.clear() : selection.selectAll(all))}
      >
        {everything ? "Select none" : "Select all"}
      </Button>
      <Button
        variant="destructive"
        size="xs"
        disabled={selection.count === 0}
        onClick={() => onForget(selection.subjects)}
      >
        Forget selected ({selection.count})
      </Button>
      <Button variant="ghost" size="xs" onClick={() => selection.setActive(false)}>
        Done
      </Button>
    </div>
  )
}

/** A row with a checkbox in front of it. */
export function SelectableRow({ checked, onToggle, label, children }: {
  checked: boolean | "indeterminate"
  onToggle: () => void
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-start gap-2">
      <Checkbox className="mt-2 shrink-0" checked={checked} onCheckedChange={onToggle} aria-label={label} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

export function PassageSelectList({ results, selection }: { results: KBQueryResult[]; selection: ForgetSelection }) {
  const groups = useMemo(() => groupByDocument(results), [results])
  if (groups.length === 0) {
    return <p className="px-4 py-6 text-center text-sm text-muted-foreground">Nothing here can be forgotten.</p>
  }
  return (
    <ul className="divide-y" aria-label="Choose what to forget">
      {groups.map((g) => {
        const passages = g.passages.map((p) => p.subject)
        return (
          <li key={g.doc.id} className="px-4 py-2">
            <SelectableRow
              checked={(() => {
                const s = selection.documentState(g.doc, passages)
                return s === "indeterminate" ? "indeterminate" : s === "checked"
              })()}
              onToggle={() => selection.toggleDocument(g.doc, passages)}
              label={`Whole document ${g.filename}`}
            >
              <p className="pt-1.5 text-sm font-medium break-words">{g.filename}</p>
              <p className="text-xs text-muted-foreground">{g.domain} · whole document</p>
            </SelectableRow>
            <ul className="mt-1 space-y-1 pl-6">
              {g.passages.map((p) => (
                <li key={p.subject.id}>
                  <SelectableRow
                    checked={selection.isSelected(g.doc) || selection.isSelected(p.subject)}
                    onToggle={() => selection.togglePassage(g.doc, p.subject, passages)}
                    label={`Passage from ${g.filename}`}
                  >
                    <p className="pt-1.5 text-xs text-muted-foreground line-clamp-3">{p.text || "No preview"}</p>
                  </SelectableRow>
                </li>
              ))}
            </ul>
          </li>
        )
      })}
    </ul>
  )
}
