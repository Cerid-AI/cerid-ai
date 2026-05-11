// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * usePackInstall — tanstack-react-query mutation for installing a knowledge
 * pack inside the setup wizard "Try a sample pack" tab.
 *
 * The backend install endpoint (POST /knowledge_packs/{id}/install) is
 * synchronous (packs are small, < a few MB) and returns 200 with the
 * InstallKnowledgePackResponse shape on success. No SSE/polling needed.
 *
 * Usage:
 *   const { install, isPending, isSuccess, error, installedPackId } =
 *     usePackInstall()
 *   <button onClick={() => install("python-stdlib-docs")} />
 */

import { useState, useCallback } from "react"
import { useMutation } from "@tanstack/react-query"
import { installKnowledgePack } from "@/lib/api/knowledge-packs"
import { logSwallowedError } from "@/lib/log-swallowed"

export interface UsePackInstallReturn {
  /** Trigger pack installation. Pass the pack id from the registry. */
  install: (packId: string) => Promise<void>
  /** True while the install POST is in flight. */
  isPending: boolean
  /** True once the install completed successfully. */
  isSuccess: boolean
  /** Error from the install mutation, if any. */
  error: Error | null
  /** The id of the pack that was successfully installed (null until done). */
  installedPackId: string | null
  /** Reset state so another pack can be installed. */
  reset: () => void
}

export function usePackInstall(): UsePackInstallReturn {
  const [installedPackId, setInstalledPackId] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (packId: string) => installKnowledgePack(packId),
    onSuccess: (data) => {
      setInstalledPackId(data.pack_id)
    },
    onError: (err) => {
      logSwallowedError(
        err instanceof Error ? err : new Error(String(err)),
        "use-pack-install",
      )
    },
  })

  const install = useCallback(
    async (packId: string): Promise<void> => {
      await mutation.mutateAsync(packId)
    },
    // mutation.mutateAsync is stable across renders
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mutation.mutateAsync],
  )

  const reset = useCallback(() => {
    mutation.reset()
    setInstalledPackId(null)
  }, [mutation])

  return {
    install,
    isPending: mutation.isPending,
    isSuccess: mutation.isSuccess,
    error: mutation.error instanceof Error ? mutation.error : null,
    installedPackId,
    reset,
  }
}
