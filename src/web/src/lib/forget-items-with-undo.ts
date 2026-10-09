// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type { QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ForgetConflictError, ForgetHttpError, forgetSubjects, restoreForget } from "@/lib/api"
import type { ForgetMode, ForgetSubject } from "@/lib/api"
import { QUERY_KEYS } from "@/lib/query-keys"

/** Every query that can show a document, a passage or a memory, and the Trash. */
const KB_QUERY_KEYS: readonly (readonly string[])[] = [
  ["kb-search"], ["kb-query"], ["orchestrated-query"], ["artifacts"], ["taxonomy"],
  QUERY_KEYS.forgetTrash(), QUERY_KEYS.forgetReceipts(),
]

function refresh(queryClient: QueryClient): void {
  for (const queryKey of KB_QUERY_KEYS) void queryClient.invalidateQueries({ queryKey })
}

/** Forget documents, passages and memories picked from search, and say what
 *  happened, with Undo after a move to Trash. Returns whether it went through. */
export async function forgetItemsWithUndo(
  queryClient: QueryClient,
  subjects: ForgetSubject[],
  mode: ForgetMode,
): Promise<boolean> {
  let result
  try {
    result = await forgetSubjects(subjects, mode)
  } catch (err) {
    toast.error(
      err instanceof ForgetHttpError && err.status < 500
        ? `Couldn't forget: ${err.message}`
        : "Couldn't reach the server. Nothing was forgotten.",
    )
    return false
  }
  refresh(queryClient)
  if (mode === "permanent") {
    toast.success(
      result.state === "purged"
        ? "Forgotten permanently"
        : "Forgotten. Some stores are still erasing and will finish shortly.",
    )
    return true
  }
  toast.success("Moved to Trash", {
    action: {
      label: "Undo",
      onClick: () => {
        restoreForget(result.forget_id)
          .then(() => refresh(queryClient))
          .catch((err: unknown) => {
            toast.error(
              err instanceof ForgetConflictError
                ? "This has started erasing and can't be restored."
                : "Couldn't restore. Try again from Settings → Data.",
            )
          })
      },
    },
  })
  return true
}
