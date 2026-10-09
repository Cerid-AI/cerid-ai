// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useForgetItemsFlow } from "@/hooks/use-forget-items-flow"
import { useForgetSelection } from "@/hooks/use-forget-selection"
import type { SelectState } from "@/hooks/use-forget-selection"
import { searchForgetAssist } from "@/lib/api"
import type { AssistCandidate, AssistGroup, AssistResult, ForgetSubject } from "@/lib/api"

const STORE_LABEL: Record<AssistCandidate["store"], string> = {
  knowledge_base: "Document",
  memories: "Memory",
  conversations: "Conversation",
}

const subjectOf = (c: { kind: ForgetSubject["kind"]; id: string }): ForgetSubject => ({ kind: c.kind, id: c.id })
const passagesOf = (c: AssistCandidate): ForgetSubject[] => c.passages.map(subjectOf)

type Selection = ReturnType<typeof useForgetSelection>

function itemState(sel: Selection, c: AssistCandidate): SelectState {
  if (c.kind === "artifact") return sel.documentState(subjectOf(c), passagesOf(c))
  return sel.isSelected(subjectOf(c)) ? "checked" : "unchecked"
}

function groupState(sel: Selection, g: AssistGroup): SelectState {
  const states = g.items.map((c) => itemState(sel, c))
  if (states.length > 0 && states.every((s) => s === "checked")) return "checked"
  return states.some((s) => s !== "unchecked") ? "indeterminate" : "unchecked"
}

function toggleGroup(sel: Selection, g: AssistGroup): void {
  const whole = g.items.map(subjectOf)
  const passages = g.items.flatMap(passagesOf)
  if (groupState(sel, g) === "checked") sel.update([], [...whole, ...passages])
  else sel.update(whole, passages)
}

const asChecked = (s: SelectState) => (s === "indeterminate" ? "indeterminate" : s === "checked")

function Candidate({ c, sel }: { c: AssistCandidate; sel: Selection }) {
  const id = `forget-assist-${c.kind}-${c.id}`
  const toggle = () => (c.kind === "artifact"
    ? sel.toggleDocument(subjectOf(c), passagesOf(c))
    : sel.toggle(subjectOf(c)))
  return (
    <li className="space-y-1">
      <div className="flex items-start gap-2">
        <Checkbox id={id} className="mt-0.5" checked={asChecked(itemState(sel, c))} onCheckedChange={toggle} />
        <div className="min-w-0 flex-1">
          <label htmlFor={id} className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="break-words font-medium">{c.label}</span>
            <Badge variant="outline" className="text-label-xxs px-1 py-0">{STORE_LABEL[c.store]}</Badge>
            {c.domain && c.store === "knowledge_base" && (
              <span className="text-label-xs text-muted-foreground">{c.domain}</span>
            )}
          </label>
          {c.excerpt && <p className="text-xs text-muted-foreground line-clamp-2">{c.excerpt}</p>}
          {c.reason && <p className="text-label-xs text-muted-foreground/80">{c.reason}</p>}
        </div>
      </div>
      {c.kind === "artifact" && c.passages.length > 0 && (
        <ul className="space-y-1 pl-6" aria-label={`Passages of ${c.label}`}>
          {c.passages.map((p) => {
            const pid = `forget-assist-chunk-${p.id}`
            return (
              <li key={p.id} className="flex items-start gap-2">
                <Checkbox
                  id={pid}
                  className="mt-0.5"
                  checked={sel.isSelected(subjectOf(c)) || sel.isSelected(subjectOf(p))}
                  onCheckedChange={() => sel.togglePassage(subjectOf(c), subjectOf(p), passagesOf(c))}
                  aria-label={`Only this passage of ${c.label}`}
                />
                <label htmlFor={pid} className="text-xs text-muted-foreground line-clamp-2">
                  {p.excerpt || "No preview"}
                </label>
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
}

function Group({ g, sel, index }: { g: AssistGroup; sel: Selection; index: number }) {
  const id = `forget-assist-group-${index}`
  return (
    <section aria-labelledby={id} className="space-y-2 rounded-md border p-3">
      <div className="flex items-start gap-2">
        <Checkbox
          className="mt-0.5"
          checked={asChecked(groupState(sel, g))}
          onCheckedChange={() => toggleGroup(sel, g)}
          aria-label={`Everything in ${g.title}`}
        />
        <div className="min-w-0">
          <h3 id={id} className="text-sm font-medium">{g.title} <span className="text-muted-foreground">({g.items.length})</span></h3>
          {g.explanation && <p className="text-xs text-muted-foreground">{g.explanation}</p>}
        </div>
      </div>
      <ul className="space-y-2 pl-6">
        {g.items.map((c) => <Candidate key={`${c.kind}:${c.id}`} c={c} sel={sel} />)}
      </ul>
    </section>
  )
}

/** Describe what to forget; Cerid finds it, the local model sorts it, and the
 *  person picks what goes before the usual review and Trash. */
export function ForgetAssistant() {
  const [scope, setScope] = useState("")
  const [declined, setDeclined] = useState(false)
  const sel = useForgetSelection()
  const { clear } = sel
  const search = useMutation({
    mutationFn: ({ text, allowCloud }: { text: string; allowCloud: boolean }) => searchForgetAssist(text, allowCloud),
    // A consented re-run sorts the same matches; the picks made meanwhile stay.
    onSuccess: (_data, vars) => { if (!vars.allowCloud) clear(); setDeclined(false) },
  })
  const flow = useForgetItemsFlow(useCallback(() => { clear(); search.reset() }, [clear, search]), "agent")
  const result: AssistResult | undefined = search.data
  const text = scope.trim()

  const find = () => {
    if (text.length < 2) return
    search.mutate({ text, allowCloud: false })
  }
  // Consent covers the description and snippets the prompt showed, not
  // whatever the box holds now.
  const sendToCloud = (shown: AssistResult) => search.mutate({ text: shown.scope, allowCloud: true })

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Describe what you want forgotten. Cerid searches your documents, memories and conversations, and the local
        model sorts what it finds. Nothing is removed until you review it.
      </p>
      <form
        className="flex gap-2"
        onSubmit={(e) => { e.preventDefault(); find() }}
        role="search"
        aria-label="Forget with the assistant"
      >
        <Input
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          placeholder="Everything about my old 401(k) plan"
          aria-label="What should Cerid forget?"
          maxLength={300}
        />
        <Button type="submit" disabled={text.length < 2 || search.isPending}>
          <Search className="mr-1 h-3.5 w-3.5" />
          Find
        </Button>
      </form>

      {search.isPending && (
        <div role="status" aria-label="Searching" className="space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      )}

      {search.isError && (
        <p className="text-sm text-destructive" role="alert">Couldn't search. Try again.</p>
      )}

      {result && !search.isPending && (
        <div className="space-y-3">
          {result.status === "needs_consent" && !declined && (
            <div role="alert" className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
              <p className="text-sm">
                The local model isn't available to sort these. Send your description and these
                {" "}{result.total} snippets to <span className="font-medium">{result.cloud_model || "the cloud model"}</span> to
                group them?
              </p>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => sendToCloud(result)}>Send to the cloud model</Button>
                <Button size="sm" variant="ghost" onClick={() => setDeclined(true)}>Show them unsorted</Button>
              </div>
            </div>
          )}
          {result.status === "ungrouped" && result.reason && (
            <p className="text-xs text-muted-foreground">{result.reason} The matches are shown unsorted.</p>
          )}
          {result.model === "cloud" && (
            <p className="text-xs text-muted-foreground">Sorted by {result.cloud_model || "the cloud model"}, as you allowed.</p>
          )}
          {result.total === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing matches &ldquo;{result.scope}&rdquo;.</p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Check what should go. To narrow the search, change the wording and search again.
              </p>
              {result.groups.map((g, i) => <Group key={`${g.title}-${i}`} g={g} sel={sel} index={i} />)}
              <div className="flex justify-end">
                <Button disabled={sel.count === 0} onClick={() => flow.start(sel.subjects)}>
                  Review and forget ({sel.count})
                </Button>
              </div>
            </>
          )}
        </div>
      )}
      {flow.dialog}
    </div>
  )
}
