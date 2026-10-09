// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useMemo, useState } from "react"
import type { ForgetSubject } from "@/lib/api"
import type { KBQueryResult, MemoryRecallResult } from "@/lib/types"

export type SelectState = "checked" | "unchecked" | "indeterminate"

const keyOf = (s: ForgetSubject) => `${s.kind}:${s.id}`

/** The passage to forget for a search result. A child chunk stands for its
 *  parent: the parent's text contains it and is what retrieval serves for every
 *  sibling. The server resolves children the same way. */
export function passageSubject(result: KBQueryResult): ForgetSubject | null {
  if (result.source_type === "external") return null
  const id = result.parent_chunk_id || result.chunk_id
  // Graph community summaries are synthesized per query and stored nowhere.
  return id && !id.startsWith("community:") ? { kind: "chunk", id } : null
}

export function documentSubject(result: Pick<KBQueryResult, "artifact_id">): ForgetSubject {
  return { kind: "artifact", id: result.artifact_id }
}

export function memorySubject(memory: MemoryRecallResult): ForgetSubject {
  return { kind: memory.forget_kind ?? "artifact", id: memory.memory_id }
}

export interface ForgetSelection {
  active: boolean
  setActive: (on: boolean) => void
  count: number
  subjects: ForgetSubject[]
  isSelected: (s: ForgetSubject) => boolean
  toggle: (s: ForgetSubject) => void
  /** A document and the passages of it on screen. Choosing the document
   *  replaces its passage choices; unchoosing a passage of a chosen document
   *  keeps the document's other passages chosen instead. */
  documentState: (doc: ForgetSubject, passages: ForgetSubject[]) => SelectState
  toggleDocument: (doc: ForgetSubject, passages: ForgetSubject[]) => void
  togglePassage: (doc: ForgetSubject, passage: ForgetSubject, passages: ForgetSubject[]) => void
  selectAll: (subjects: ForgetSubject[]) => void
  clear: () => void
}

/** Selection mode for forgetting search results. Leaving the mode clears it. */
export function useForgetSelection(): ForgetSelection {
  const [active, setActiveState] = useState(false)
  const [chosen, setChosen] = useState<Map<string, ForgetSubject>>(new Map())

  const clear = useCallback(() => setChosen(new Map()), [])
  const setActive = useCallback((on: boolean) => {
    setActiveState(on)
    if (!on) setChosen(new Map())
  }, [])

  const isSelected = useCallback((s: ForgetSubject) => chosen.has(keyOf(s)), [chosen])

  const toggle = useCallback((s: ForgetSubject) => {
    setChosen((prev) => {
      const next = new Map(prev)
      if (next.has(keyOf(s))) next.delete(keyOf(s))
      else next.set(keyOf(s), s)
      return next
    })
  }, [])

  const documentState = useCallback((doc: ForgetSubject, passages: ForgetSubject[]): SelectState => {
    if (chosen.has(keyOf(doc))) return "checked"
    return passages.some((p) => chosen.has(keyOf(p))) ? "indeterminate" : "unchecked"
  }, [chosen])

  const toggleDocument = useCallback((doc: ForgetSubject, passages: ForgetSubject[]) => {
    setChosen((prev) => {
      const next = new Map(prev)
      for (const p of passages) next.delete(keyOf(p))
      if (prev.has(keyOf(doc))) next.delete(keyOf(doc))
      else next.set(keyOf(doc), doc)
      return next
    })
  }, [])

  const togglePassage = useCallback((doc: ForgetSubject, passage: ForgetSubject, passages: ForgetSubject[]) => {
    setChosen((prev) => {
      const next = new Map(prev)
      if (prev.has(keyOf(doc))) {
        next.delete(keyOf(doc))
        for (const p of passages) if (keyOf(p) !== keyOf(passage)) next.set(keyOf(p), p)
        return next
      }
      if (next.has(keyOf(passage))) next.delete(keyOf(passage))
      else next.set(keyOf(passage), passage)
      return next
    })
  }, [])

  const selectAll = useCallback((subjects: ForgetSubject[]) => {
    setChosen(new Map(subjects.map((s) => [keyOf(s), s])))
  }, [])

  const subjects = useMemo(() => [...chosen.values()], [chosen])

  return {
    active, setActive, count: chosen.size, subjects, isSelected, toggle,
    documentState, toggleDocument, togglePassage, selectAll, clear,
  }
}

export interface DocumentGroup {
  doc: ForgetSubject
  filename: string
  domain: string
  passages: { subject: ForgetSubject; text: string }[]
}

function excerpt(text: string): string {
  const flat = text.replace(/^\[[^\n]*\]\n/, "").replace(/^(?:Source|Domain|Category): [^\n]*\n\n/, "")
  return flat.replace(/\s+/g, " ").trim().slice(0, 200)
}

/** Search results grouped by document, every returned passage listed, for
 *  choosing what to forget. A document's box chooses the whole document. */
export function groupByDocument(results: KBQueryResult[]): DocumentGroup[] {
  const groups = new Map<string, DocumentGroup>()
  for (const r of results) {
    if (!r.artifact_id || r.source_type === "external" || r.artifact_id.startsWith("community:")) continue
    let group = groups.get(r.artifact_id)
    if (!group) {
      group = { doc: documentSubject(r), filename: r.filename, domain: r.domain, passages: [] }
      groups.set(r.artifact_id, group)
    }
    const subject = passageSubject(r)
    if (subject && !group.passages.some((p) => p.subject.id === subject.id)) {
      group.passages.push({ subject, text: excerpt(r.content || "") })
    }
  }
  return [...groups.values()]
}

/** What Select all chooses: whole documents, each covering its passages. */
export function documentSubjects(groups: DocumentGroup[]): ForgetSubject[] {
  return groups.map((g) => g.doc)
}
