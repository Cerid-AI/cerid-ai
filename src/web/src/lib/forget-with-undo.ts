// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { toast } from "sonner"
import { ForgetConflictError } from "@/lib/api"
import type { ForgetMode, ForgetSubject } from "@/lib/api"
import type { ForgetOutcome } from "@/hooks/use-conversations"
import type { Conversation } from "@/lib/types"

export interface ForgetWithUndoDeps {
  conversations: Conversation[]
  forget: (ids: string[], opts: { mode: ForgetMode; derived?: ForgetSubject[] }) => Promise<ForgetOutcome>
  restore: (forgetId: string, conversations: Conversation[]) => Promise<void>
}

/** Forget conversations and tell the user what happened, with Undo after a
 *  move to Trash. The removed conversations are captured first so Undo can put
 *  them back on this client. */
export async function forgetWithUndo(
  deps: ForgetWithUndoDeps,
  ids: string[],
  mode: ForgetMode,
  derived: ForgetSubject[],
): Promise<void> {
  const removed = deps.conversations.filter((c) => ids.includes(c.id))
  const outcome = await deps.forget(ids, { mode, derived })

  if (outcome.status === "deferred") {
    toast.success("Deleted on this device", {
      description: mode === "permanent" || derived.length > 0
        ? "It moves to the Trash when Private Mode is off. Erase it from Settings → Data then; related memories are kept until you do."
        : "It moves to the Trash when Private Mode is off.",
    })
    return
  }
  if (outcome.status === "refused") {
    toast.error(`Couldn't delete: ${outcome.message}`)
    return
  }
  if (outcome.status === "failed") {
    toast.error(
      "Couldn't reach the server. The chat will move to the Trash when it reconnects; related memories were not removed.",
    )
    return
  }
  const { result } = outcome
  if (mode === "permanent") {
    toast.success(
      result.state === "purged"
        ? "Forgotten permanently"
        : "Forgotten. Some stores are still erasing and will finish shortly.",
    )
    return
  }
  toast.success("Moved to Trash", {
    action: {
      label: "Undo",
      onClick: () => {
        deps.restore(result.forget_id, removed).catch((err: unknown) => {
          toast.error(
            err instanceof ForgetConflictError
              ? "This has started erasing and can't be restored."
              : "Couldn't restore. Try again from Settings → Data.",
          )
        })
      },
    },
  })
}
