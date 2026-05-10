// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DomainBadge } from "@/components/ui/domain-badge"
import { EmptyState } from "@/components/ui/empty-state"
import { CheckCircle2, Loader2, Library, Trash2, AlertCircle, Download, Info } from "lucide-react"
import { formatFileSize } from "@/lib/utils"
import {
  fetchKnowledgePackRegistry,
  fetchInstalledKnowledgePacks,
  installKnowledgePack,
  uninstallKnowledgePack,
  type KnowledgePackSummary,
  type InstalledKnowledgePack,
} from "@/lib/api"

interface KnowledgeLibraryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function KnowledgeLibraryDialog({ open, onOpenChange }: KnowledgeLibraryDialogProps) {
  const queryClient = useQueryClient()
  const [busyPackId, setBusyPackId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const registryQuery = useQuery({
    queryKey: ["knowledge-packs", "registry"],
    queryFn: fetchKnowledgePackRegistry,
    enabled: open,
  })

  const installedQuery = useQuery({
    queryKey: ["knowledge-packs", "installed"],
    queryFn: fetchInstalledKnowledgePacks,
    enabled: open,
  })

  const installMutation = useMutation({
    mutationFn: (packId: string) => installKnowledgePack(packId),
    onMutate: (packId) => {
      setBusyPackId(packId)
      setErrorMessage(null)
    },
    onSuccess: () => {
      // Refresh installed list + the artifact pages so newly-ingested
      // content shows up in the KB pane immediately.
      queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] })
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
    },
    onError: (err: Error) => setErrorMessage(err.message),
    onSettled: () => setBusyPackId(null),
  })

  const uninstallMutation = useMutation({
    mutationFn: (packId: string) => uninstallKnowledgePack(packId),
    onMutate: (packId) => {
      setBusyPackId(packId)
      setErrorMessage(null)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] })
      queryClient.invalidateQueries({ queryKey: ["artifacts"] })
    },
    onError: (err: Error) => setErrorMessage(err.message),
    onSettled: () => setBusyPackId(null),
  })

  const installedById = new Map<string, InstalledKnowledgePack>()
  installedQuery.data?.packs.forEach((p) => installedById.set(p.pack_id, p))

  const packsByDomain = registryQuery.data?.packs_by_domain ?? {}
  const sortedDomains = Object.keys(packsByDomain).sort()
  const totalAvailable = sortedDomains.reduce((n, d) => n + packsByDomain[d].length, 0)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Library className="h-5 w-5" />
            Knowledge Library
          </DialogTitle>
          <DialogDescription>
            Optional, curator-published baseline corpora. Pick the
            domains relevant to you — packs install through the same
            ingestion pipeline as your own files (dedup, layout-aware
            parsing, quality scoring all apply).
          </DialogDescription>
        </DialogHeader>

        {errorMessage && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        <Tabs defaultValue="available" className="flex-1">
          <TabsList>
            <TabsTrigger value="available">
              Available <Badge variant="secondary" className="ml-2">{totalAvailable}</Badge>
            </TabsTrigger>
            <TabsTrigger value="installed">
              Installed <Badge variant="secondary" className="ml-2">{installedById.size}</Badge>
            </TabsTrigger>
          </TabsList>

          <TabsContent value="available" className="mt-3">
            <ScrollArea className="max-h-[460px] pr-2">
              {registryQuery.isLoading && (
                <div className="flex items-center justify-center p-8 text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading registry…
                </div>
              )}
              {!registryQuery.isLoading && totalAvailable === 0 && (
                <EmptyState
                  icon={Info}
                  title="No packs in registry"
                  description="The shipped registry is intentionally empty. Set CERID_KNOWLEDGE_PACKS_REGISTRY to a curated registry, or wait for community packs to land."
                />
              )}
              {sortedDomains.map((domain) => (
                <div key={domain} className="mb-4">
                  <div className="mb-2 flex items-center gap-2">
                    <DomainBadge domain={domain} />
                    <span className="text-xs text-muted-foreground">
                      {packsByDomain[domain].length} pack{packsByDomain[domain].length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {packsByDomain[domain].map((pack) => (
                      <PackCard
                        key={pack.id}
                        pack={pack}
                        installed={installedById.get(pack.id)}
                        busy={busyPackId === pack.id}
                        onInstall={() => installMutation.mutate(pack.id)}
                        onUninstall={() => uninstallMutation.mutate(pack.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </ScrollArea>
          </TabsContent>

          <TabsContent value="installed" className="mt-3">
            <ScrollArea className="max-h-[460px] pr-2">
              {installedQuery.isLoading && (
                <div className="flex items-center justify-center p-8 text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading installed packs…
                </div>
              )}
              {!installedQuery.isLoading && installedById.size === 0 && (
                <EmptyState
                  icon={Library}
                  title="No knowledge packs installed"
                  description="Browse the Available tab to install curated corpora. The repo ships slim — packs are entirely optional."
                />
              )}
              {Array.from(installedById.values()).map((pack) => (
                <InstalledPackCard
                  key={pack.pack_id}
                  pack={pack}
                  busy={busyPackId === pack.pack_id}
                  onUninstall={() => uninstallMutation.mutate(pack.pack_id)}
                />
              ))}
            </ScrollArea>
          </TabsContent>
        </Tabs>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PackCardProps {
  pack: KnowledgePackSummary
  installed: InstalledKnowledgePack | undefined
  busy: boolean
  onInstall: () => void
  onUninstall: () => void
}

function PackCard({ pack, installed, busy, onInstall, onUninstall }: PackCardProps) {
  const isInstalled = Boolean(installed)
  const sameVersion = installed?.version === pack.version

  return (
    <div className="flex items-start justify-between gap-3 rounded-md border p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{pack.name}</span>
          <Badge variant="outline" className="text-xs">v{pack.version}</Badge>
          {isInstalled && sameVersion && (
            <Badge variant="default" className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-400">
              <CheckCircle2 className="mr-1 h-3 w-3" /> Installed
            </Badge>
          )}
          {isInstalled && !sameVersion && (
            <Badge variant="secondary">Update available</Badge>
          )}
        </div>
        {pack.description && (
          <p className="mt-1 text-sm text-muted-foreground">{pack.description}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
          {pack.size_bytes > 0 && <span>{formatFileSize(pack.size_bytes)}</span>}
          {pack.artifact_count > 0 && <span>{pack.artifact_count} artifacts</span>}
          {pack.license && <span>license: {pack.license}</span>}
          {pack.provenance?.source && <span>source: {pack.provenance.source}</span>}
        </div>
      </div>
      <div className="flex flex-shrink-0 gap-2">
        {isInstalled ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={onUninstall}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            <span className="ml-1">Uninstall</span>
          </Button>
        ) : (
          <Button size="sm" disabled={busy} onClick={onInstall}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            <span className="ml-1">Install</span>
          </Button>
        )}
      </div>
    </div>
  )
}

interface InstalledPackCardProps {
  pack: InstalledKnowledgePack
  busy: boolean
  onUninstall: () => void
}

function InstalledPackCard({ pack, busy, onUninstall }: InstalledPackCardProps) {
  return (
    <div className="mb-2 flex items-start justify-between gap-3 rounded-md border p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{pack.pack_id}</span>
          <Badge variant="outline" className="text-xs">v{pack.version}</Badge>
          <DomainBadge domain={pack.domain} />
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {pack.artifact_count} artifacts · installed {new Date(pack.installed_at).toLocaleString()}
        </div>
      </div>
      <Button size="sm" variant="outline" disabled={busy} onClick={onUninstall}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        <span className="ml-1">Uninstall</span>
      </Button>
    </div>
  )
}
