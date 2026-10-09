// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useMemo, useState } from "react"
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
import { previewForget } from "@/lib/api"
import type { ForgetMode, ForgetPreview, ForgetSubject, PreviewGroup, PreviewItem } from "@/lib/api"

const HEADINGS: Partial<Record<PreviewGroup["key"], string>> = {
  transcripts: "Chat transcripts",
  memories: "Memories from this chat",
  summary: "Session summary",
  verified_memories: "Verified memories",
  cited_documents: "Documents this chat cited",
}

const key = (s: ForgetSubject) => `${s.kind}:${s.id}`
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function defaultChecked(group: PreviewGroup, item: PreviewItem): boolean {
  if (group.default === "always") return false
  return (item.default ?? group.default) === "checked"
}

function itemNote(group: PreviewGroup, item: PreviewItem): string | null {
  if (group.key === "memories" && item.shared_with) {
    return `Also from ${plural(item.shared_with, "other conversation")}`
  }
  if (group.key === "cited_documents") {
    return `Used by ${plural(item.used_by ?? 0, "other conversation")} · forgets the whole document`
  }
  return null
}

interface ForgetConversationDialogProps {
  conversationId: string | null
  title: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (mode: ForgetMode, derived: ForgetSubject[]) => void
}

type PreviewState = { status: "loading" } | { status: "error" } | { status: "ready"; preview: ForgetPreview }

/** The conversation delete dialog: what the chat produced, which of it goes
 *  with it, and whether it moves to the Trash or is forgotten permanently. */
export function ForgetConversationDialog({
  conversationId, title, open, onOpenChange, onConfirm,
}: ForgetConversationDialogProps) {
  const [state, setState] = useState<PreviewState>({ status: "loading" })
  const [selected, setSelected] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!open || !conversationId) return
    let cancelled = false
    setState({ status: "loading" })
    previewForget(conversationId)
      .then((preview) => {
        if (cancelled) return
        const initial = new Set<string>()
        for (const group of preview.groups) {
          for (const item of group.items) if (defaultChecked(group, item)) initial.add(key(item))
        }
        setSelected(initial)
        setState({ status: "ready", preview })
      })
      .catch(() => { if (!cancelled) setState({ status: "error" }) })
    return () => { cancelled = true }
  }, [open, conversationId])

  const derived = useMemo<ForgetSubject[]>(() => {
    if (state.status !== "ready") return []
    return state.preview.groups
      .filter((g) => g.key !== "transcripts")
      .flatMap((g) => g.items)
      .filter((i) => selected.has(key(i)))
      .map(({ kind, id }) => ({ kind, id }))
  }, [state, selected])

  const toggle = (item: PreviewItem, on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (on) next.add(key(item))
      else next.delete(key(item))
      return next
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Delete this conversation?</DialogTitle>
          <DialogDescription>
            {title ? <span className="font-medium text-foreground">{title}. </span> : null}
            Move to Trash hides it everywhere, and you can restore it from Settings → Data.
            Forget permanently erases it now and keeps a receipt.
          </DialogDescription>
        </DialogHeader>

        {state.status === "loading" && (
          <div role="status" aria-label="Loading what this chat produced" className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/5" />
          </div>
        )}

        {state.status === "error" && (
          <p className="text-sm text-muted-foreground">Couldn't load what this chat produced.</p>
        )}

        {state.status === "ready" && (
          <div className="space-y-4">
            {state.preview.groups.filter((g) => g.items.length > 0).map((group) => (
              <section key={group.key} aria-labelledby={`forget-group-${group.key}`} className="space-y-1.5">
                <h3 id={`forget-group-${group.key}`} className="text-sm font-medium">{HEADINGS[group.key] ?? group.key}</h3>
                {group.key === "transcripts" ? (
                  <p className="text-xs text-muted-foreground">
                    {plural(group.items.length, "transcript")}, always removed with the chat
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {group.items.map((item) => {
                      const id = `forget-item-${item.kind}-${item.id}`
                      const note = itemNote(group, item)
                      return (
                        <li key={key(item)} className="flex items-start gap-2">
                          <Checkbox
                            id={id}
                            className="mt-0.5"
                            checked={selected.has(key(item))}
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
                )}
                {group.key === "memories" && state.preview.derived_facts > 0 && (
                  <p className="text-xs text-muted-foreground">
                    And {plural(state.preview.derived_facts, "fact")} derived only from these memories
                  </p>
                )}
              </section>
            ))}
          </div>
        )}

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          {state.status === "error" ? (
            <Button onClick={() => onConfirm("trash", [])}>Move to Trash anyway</Button>
          ) : (
            <>
              <Button
                variant="destructive"
                disabled={state.status !== "ready"}
                onClick={() => onConfirm("permanent", derived)}
              >
                Forget permanently
              </Button>
              <Button disabled={state.status !== "ready"} onClick={() => onConfirm("trash", derived)}>
                Move to Trash
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
