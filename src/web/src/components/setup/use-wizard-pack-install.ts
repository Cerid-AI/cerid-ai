// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * useWizardPackInstall — pack installation for the setup-wizard surfaces
 * (Build Knowledge step + "Try a sample pack" tab).
 *
 * Speaks the async install contract: POST /knowledge_packs/{id}/install
 * returns 202 {job_id, status:"queued"} on new backends, and the hook then
 * polls GET /knowledge_packs/registry (~2s) until the pack's `installing`
 * flag clears. Two legacy/edge responses resolve immediately:
 * 200 {status:"already_installed"} and the old synchronous 200 body.
 *
 * Wizard-scoped on purpose — the KB-pane pack surfaces have their own
 * owners; this hook lives under components/setup/ so those surfaces are
 * untouched.
 */

import { useCallback, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { fetchKnowledgePackRegistry, type KnowledgePackSummary } from "@/lib/api/knowledge-packs"
import { startPackInstall } from "@/lib/api/setup"
import { logSwallowedError } from "@/lib/log-swallowed"

// ---------------------------------------------------------------------------
// Registry install-state flags
// ---------------------------------------------------------------------------

export interface PackInstallFlags {
  installed: boolean
  installing: boolean
}

/**
 * Registry entries from newer backends carry `installed` / `installing`
 * booleans; older backends omit them. Missing fields read as "not installed
 * / not installing" so the wizard still behaves against an old backend.
 */
export function packInstallFlags(pack: KnowledgePackSummary): PackInstallFlags {
  const p = pack as KnowledgePackSummary & { installed?: unknown; installing?: unknown }
  return { installed: p.installed === true, installing: p.installing === true }
}

/** Does any entry in a registry response report an in-flight install?
 *  Drives the ~2s registry refetch interval on the wizard pack cards. */
export function registryHasInstalling(
  registry: { packs_by_domain: Record<string, KnowledgePackSummary[]> } | undefined,
): boolean {
  if (!registry) return false
  return Object.values(registry.packs_by_domain)
    .flat()
    .some((p) => packInstallFlags(p).installing)
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseWizardPackInstallOptions {
  /** Registry poll cadence while a queued install runs (default 2s). */
  pollIntervalMs?: number
  /** Poll budget before declaring a timeout (default ≈5 min at 2s). */
  maxPolls?: number
}

export interface WizardPackInstallOutcome {
  /** True when the backend reported the pack was already installed. */
  alreadyInstalled: boolean
}

export interface UseWizardPackInstallReturn {
  /** Start an install; resolves once the pack is installed, rejects on failure/timeout. */
  install: (packId: string) => Promise<WizardPackInstallOutcome>
  /** True while an install (including its polling phase) is in flight. */
  isPending: boolean
  /** Pack id currently being installed by this hook instance, else null. */
  installingPackId: string | null
  /** True once the last install completed successfully. */
  isSuccess: boolean
  /** Error from the last install attempt, if any. */
  error: Error | null
  /** Id of the pack that finished installing (null until done). */
  installedPackId: string | null
  /** Clear success/error state so another pack can be installed. */
  reset: () => void
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function useWizardPackInstall(
  opts: UseWizardPackInstallOptions = {},
): UseWizardPackInstallReturn {
  const { pollIntervalMs = 2000, maxPolls = 150 } = opts
  const queryClient = useQueryClient()
  const [installingPackId, setInstallingPackId] = useState<string | null>(null)
  const [installedPackId, setInstalledPackId] = useState<string | null>(null)
  const [isSuccess, setIsSuccess] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const install = useCallback(
    async (packId: string): Promise<WizardPackInstallOutcome> => {
      setInstallingPackId(packId)
      setIsSuccess(false)
      setError(null)
      try {
        const start = await startPackInstall(packId)

        if (start.status === "queued") {
          // Async backend: poll the registry until the entry settles.
          let sawInstalling = false
          let completed = false
          for (let attempt = 0; attempt < maxPolls; attempt++) {
            await sleep(pollIntervalMs)
            let entry: KnowledgePackSummary | undefined
            try {
              const registry = await fetchKnowledgePackRegistry()
              entry = Object.values(registry.packs_by_domain)
                .flat()
                .find((p) => p.id === packId)
            } catch {
              continue // transient poll failure — retry within the budget
            }
            if (!entry) continue
            const flags = packInstallFlags(entry)
            if (flags.installed) {
              completed = true
              break
            }
            if (flags.installing) {
              sawInstalling = true
              continue
            }
            // Settled without success. Only trust that as a failure once the
            // job was actually observed running — a poll can land in the gap
            // between the 202 and the registry flag flipping on.
            if (sawInstalling) {
              throw new Error("Pack install failed — check the backend logs.")
            }
          }
          if (!completed) {
            throw new Error(
              "Pack install timed out — it may still be running; check the Knowledge Library.",
            )
          }
        }

        setInstalledPackId(packId)
        setIsSuccess(true)
        // Refresh both registry cache keys used by the wizard surfaces plus
        // the artifact lists the install just grew.
        queryClient.invalidateQueries({ queryKey: ["knowledge-pack-registry"] })
        queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] })
        queryClient.invalidateQueries({ queryKey: ["artifacts"] })
        return { alreadyInstalled: start.status === "already_installed" }
      } catch (err) {
        const e = err instanceof Error ? err : new Error(String(err))
        setError(e)
        logSwallowedError(e, "use-wizard-pack-install", { packId })
        throw e
      } finally {
        setInstallingPackId(null)
      }
    },
    [maxPolls, pollIntervalMs, queryClient],
  )

  const reset = useCallback(() => {
    setInstalledPackId(null)
    setIsSuccess(false)
    setError(null)
  }, [])

  return {
    install,
    isPending: installingPackId !== null,
    installingPackId,
    isSuccess,
    error,
    installedPackId,
    reset,
  }
}
