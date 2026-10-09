// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useState } from "react"
import type { ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { ForgetItemsDialog } from "@/components/kb/forget-items-dialog"
import { forgetItemsWithUndo } from "@/lib/forget-items-with-undo"
import type { ForgetMode, ForgetSource, ForgetSubject } from "@/lib/api"

/** The forget dialog and what happens on confirm, for any surface. */
export function useForgetItemsFlow(onDone?: () => void, source: ForgetSource = "api"): {
  start: (subjects: ForgetSubject[]) => void
  dialog: ReactNode
} {
  const queryClient = useQueryClient()
  const [subjects, setSubjects] = useState<ForgetSubject[]>([])
  const [open, setOpen] = useState(false)
  const [round, setRound] = useState(0)

  const start = useCallback((next: ForgetSubject[]) => {
    if (next.length === 0) return
    setSubjects(next)
    setRound((n) => n + 1)
    setOpen(true)
  }, [])

  const confirm = useCallback((mode: ForgetMode, chosen: ForgetSubject[]) => {
    setOpen(false)
    void forgetItemsWithUndo(queryClient, chosen, mode, source).then((ok) => { if (ok) onDone?.() })
  }, [queryClient, onDone, source])

  const dialog = (
    <ForgetItemsDialog key={round} subjects={subjects} open={open} onOpenChange={setOpen} onConfirm={confirm} />
  )
  return { start, dialog }
}
