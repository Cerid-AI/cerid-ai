// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { DomainBadge } from "@/components/ui/domain-badge"
import { Library, Download, Loader2, CheckCircle2, AlertCircle, Info } from "lucide-react"
import { formatFileSize } from "@/lib/utils"
import {
  fetchKnowledgePackRegistry,
  installKnowledgePack,
  type KnowledgePackSummary,
} from "@/lib/api/knowledge-packs"
import type { FirstDocState } from "@/components/setup/first-document-step"

// ---------------------------------------------------------------------------
// Props — matches the wizard step contract used by other steps
// ---------------------------------------------------------------------------

interface BuildKnowledgeStepState {
  installedPackIds: string[]
  firstDoc: FirstDocState
}

interface BuildKnowledgeStepProps {
  state: BuildKnowledgeStepState
  onChange: (state: BuildKnowledgeStepState) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isPlanned(pack: KnowledgePackSummary): boolean {
  return String(pack.provenance?.status ?? "").toLowerCase() === "planned"
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BuildKnowledgeStep({ state, onChange }: BuildKnowledgeStepProps) {
  const queryClient = useQueryClient()
  const [busyPackId, setBusyPackId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const registryQuery = useQuery({
    queryKey: ["knowledge-packs", "registry"],
    queryFn: fetchKnowledgePackRegistry,
  })

  const installMutation = useMutation({
    mutationFn: (packId: string) => installKnowledgePack(packId),
    onMutate: (packId) => {
      setBusyPackId(packId)
      setErrorMessage(null)
    },
    onSuccess: (data, packId) => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] })
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })

      const newInstalledIds = state.installedPackIds.includes(packId)
        ? state.installedPackIds
        : [...state.installedPackIds, packId]

      const artifactCount = data.artifact_count ?? 0

      onChange({
        ...state,
        installedPackIds: newInstalledIds,
        firstDoc: {
          ...state.firstDoc,
          ingested: true,
          documentCount: Math.max(state.firstDoc.documentCount, artifactCount),
        },
      })
    },
    onError: (err: Error) => setErrorMessage(err.message),
    onSettled: () => setBusyPackId(null),
  })

  const packsByDomain = registryQuery.data?.packs_by_domain ?? {}
  const sortedDomains = Object.keys(packsByDomain).sort()

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Library className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-1 text-center text-lg font-semibold">Build Knowledge</h3>
      <p className="mb-4 text-center text-sm text-muted-foreground">
        Install curator-published knowledge packs to pre-populate your knowledge base.
        All packs run through the same ingestion pipeline as your own files.
      </p>

      {errorMessage && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      <ScrollArea className="max-h-[380px] pr-1">
        {registryQuery.isLoading && (
          <div className="flex items-center justify-center p-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading registry…
          </div>
        )}

        {!registryQuery.isLoading && sortedDomains.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
            <Info className="h-5 w-5" />
            <p>No packs in registry. You can add packs later from the Knowledge Library.</p>
          </div>
        )}

        {sortedDomains.map((domain) => (
          <div key={domain} className="mb-4">
            <div className="mb-2 flex items-center gap-2">
              <DomainBadge domain={domain} />
              <span className="text-xs text-muted-foreground">
                {packsByDomain[domain].length} pack
                {packsByDomain[domain].length === 1 ? "" : "s"}
              </span>
            </div>
            <div className="space-y-2">
              {packsByDomain[domain].map((pack) => {
                const planned = isPlanned(pack)
                const installed = state.installedPackIds.includes(pack.id)
                const busy = busyPackId === pack.id

                return (
                  <div
                    key={pack.id}
                    className="flex items-start justify-between gap-3 rounded-md border p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{pack.name}</span>
                        <Badge variant="outline" className="text-xs">v{pack.version}</Badge>
                        {installed && (
                          <Badge
                            variant="default"
                            className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-400"
                          >
                            <CheckCircle2 className="mr-1 h-3 w-3" /> Installed
                          </Badge>
                        )}
                      </div>
                      {pack.description && (
                        <p className="mt-1 text-sm text-muted-foreground">{pack.description}</p>
                      )}
                      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                        {pack.size_bytes > 0 && <span>{formatFileSize(pack.size_bytes)}</span>}
                        {pack.artifact_count > 0 && (
                          <span>{pack.artifact_count} artifacts</span>
                        )}
                        {pack.license && <span>license: {pack.license}</span>}
                      </div>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      {planned ? (
                        <Button size="sm" disabled aria-label={`${pack.name} — coming soon`}>
                          Planned
                        </Button>
                      ) : installed ? (
                        <Badge
                          variant="default"
                          className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-400 text-xs"
                        >
                          <CheckCircle2 className="mr-1 h-3 w-3" /> Done
                        </Badge>
                      ) : (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() => installMutation.mutate(pack.id)}
                          aria-label={`Install ${pack.name}`}
                        >
                          {busy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Download className="h-3.5 w-3.5" />
                          )}
                          <span className="ml-1">Install</span>
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </ScrollArea>

      {state.installedPackIds.length > 0 && (
        <p className="mt-3 text-center text-xs text-muted-foreground">
          {state.installedPackIds.length} pack
          {state.installedPackIds.length === 1 ? "" : "s"} installed — you can add more later from the Knowledge Library.
        </p>
      )}
    </>
  )
}
