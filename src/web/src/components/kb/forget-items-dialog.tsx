// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { previewForgetItems } from "@/lib/api"
import type { ForgetMode, ForgetSubject, PreviewGroup, PreviewItem } from "@/lib/api"

const HEADINGS: Partial<Record<PreviewGroup["key"], string>> = {
  documents: "Documents",
  passages: "Passages",
  memories: "Memories",
  conversations: "Conversations",
}

const key = (s: ForgetSubject) => `${s.kind}:${s.id}`
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function itemNote(group: PreviewGroup, item: PreviewItem): string | null {
  if (group.key === "documents") {
    const parts = [plural(item.passages ?? 0, "passage")]
    if (item.used_by) parts.push(`cited by ${plural(item.used_by, "conversation")}`)
    return parts.join(" · ")
  }
  if (group.key === "passages") {
    const from = item.document ? `From ${item.document}` : null
    const children = item.children ? `includes ${plural(item.children, "smaller passage")}` : null
    return [from, children].filter(Boolean).join(" · ") || null
  }
  return null
}

interface ForgetItemsDialogProps {
  subjects: ForgetSubject[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (mode: ForgetMode, subjects: ForgetSubject[]) => void
}

/** Forget documents, passages and memories picked from search: what goes,
 *  and whether it moves to the Trash or is forgotten permanently. Everything
 *  listed starts checked. Mount it with a fresh key per selection. */
export function ForgetItemsDialog({ subjects, open, onOpenChange, onConfirm }: ForgetItemsDialogProps) {
  const requestKey = useMemo(() => subjects.map(key).sort().join("|"), [subjects])
  const preview = useQuery({
    queryKey: ["forget-preview-items", requestKey],
    queryFn: () => previewForgetItems(subjects),
    enabled: open && subjects.length > 0,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set())

  const state = preview.isError
    ? ({ status: "error" } as const)
    : preview.data
      ? ({ status: "ready", preview: preview.data } as const)
      : ({ status: "loading" } as const)

  const chosen = useMemo<ForgetSubject[]>(() => {
    if (!preview.data) return []
    return preview.data.groups
      .flatMap((g) => g.items)
      .filter((i) => !unchecked.has(key(i)))
      .map(({ kind, id }) => ({ kind, id }))
  }, [preview.data, unchecked])

  const toggle = (item: PreviewItem, on: boolean) => {
    setUnchecked((prev) => {
      const next = new Set(prev)
      if (on) next.delete(key(item))
      else next.add(key(item))
      return next
    })
  }

  const empty = state.status === "ready" && state.preview.groups.every((g) => g.items.length === 0)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Forget {plural(subjects.length, "item")}?</DialogTitle>
          <DialogDescription>
            Move to Trash hides them from every search and answer, and you can restore them from Settings → Data.
            Forget permanently erases them now and keeps a receipt.
          </DialogDescription>
        </DialogHeader>

        {state.status === "loading" && (
          <div role="status" aria-label="Loading what will be forgotten" className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}

        {state.status === "error" && (
          <p className="text-sm text-muted-foreground">Couldn't load what will be forgotten.</p>
        )}

        {empty && (
          <p className="text-sm text-muted-foreground">These are already forgotten or no longer exist.</p>
        )}

        {state.status === "ready" && !empty && (
          <div className="space-y-4">
            {state.preview.groups.filter((g) => g.items.length > 0).map((group) => (
              <section key={group.key} aria-labelledby={`forget-items-${group.key}`} className="space-y-1.5">
                <h3 id={`forget-items-${group.key}`} className="text-sm font-medium">
                  {HEADINGS[group.key] ?? group.key}
                </h3>
                <ul className="space-y-1.5">
                  {group.items.map((item) => {
                    const id = `forget-item-${item.kind}-${item.id}`
                    const note = itemNote(group, item)
                    return (
                      <li key={key(item)} className="flex items-start gap-2">
                        <Checkbox
                          id={id}
                          className="mt-0.5"
                          checked={!unchecked.has(key(item))}
                          onCheckedChange={(v) => toggle(item, v === true)}
                        />
                        <div className="min-w-0">
                          <label htmlFor={id} className="block break-words text-sm">{item.label}</label>
                          {note && <p className="text-xs text-muted-foreground">{note}</p>}
                        </div>
                      </li>
                    )
                  })}
                </ul>
                {group.key === "documents" && state.preview.derived_facts > 0 && (
                  <p className="text-xs text-muted-foreground">
                    And {plural(state.preview.derived_facts, "fact")} drawn only from these documents
                  </p>
                )}
              </section>
            ))}
            {(state.preview.notes ?? []).map((note) => (
              <p key={note} className="text-xs text-muted-foreground">{note}</p>
            ))}
          </div>
        )}

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="destructive"
            disabled={state.status !== "ready" || chosen.length === 0}
            onClick={() => onConfirm("permanent", chosen)}
          >
            Forget permanently
          </Button>
          <Button
            disabled={state.status !== "ready" || chosen.length === 0}
            onClick={() => onConfirm("trash", chosen)}
          >
            Move to Trash
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
