// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SamplePackTab — "Try a sample pack" tab body for the setup wizard
 * first-document step.
 *
 * Flow:
 *   1. Fetches the pack catalog (GET /knowledge_packs/registry).
 *   2. Shows 4 featured packs as selectable cards. Packs the registry
 *      reports as installed render a disabled "Installed ✓" state.
 *   3. User clicks "Install" on a card → useWizardPackInstall runs the
 *      async install contract (202 job + ~2s registry polling; legacy
 *      synchronous 200 and "already_installed" resolve immediately).
 *   4. On success → transitions to <DemoQueriesPanel>.
 *   5. User clicks "Continue to chat" → calls onComplete(packId).
 *
 * Design constraints:
 * - shadcn/ui + lucide icons only; no hex literals; no inline style={{}}
 * - aria-live on all loading/error transitions; keyboard reachable
 * - React 19 + tanstack-react-query (useQuery for catalog)
 */

import { useState, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Loader2,
  BookOpen,
  AlertCircle,
  Database,
  FileText,
  Scale,
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  fetchKnowledgePackRegistry,
  type KnowledgePackSummary,
} from "@/lib/api/knowledge-packs"
import {
  packInstallFlags,
  registryHasInstalling,
  useWizardPackInstall,
} from "@/components/setup/use-wizard-pack-install"
import { DemoQueriesPanel } from "@/components/setup/demo-queries-panel"

// ---------------------------------------------------------------------------
// Featured packs — ordered by ascending size for fastest wizard experience.
// Sourced from config/knowledge_packs.json (all are "built", permissive license).
// ---------------------------------------------------------------------------

const FEATURED_PACK_IDS = [
  "irs-publications-curated",  //  17 KB — finance/CC0
  "18f-methods-guides",        //  27 KB — projects/CC0
  "python-stdlib-docs",        // 167 KB — coding/PSF-2.0
  "cfpb-ask",                  // 484 KB — finance/CC0
] as const

/** Format bytes to a human-readable string. */
function formatBytes(bytes: number): string {
  if (bytes === 0) return "unknown size"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1_048_576) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

/** Shorten a SPDX license id for display. */
function formatLicense(license: string): string {
  if (!license) return "Open"
  // Strip "-only" / "-or-later" SPDX suffixes for readability
  return license.replace(/-(only|or-later)$/i, "")
}

// ---------------------------------------------------------------------------
// Sub-component: single pack card
// ---------------------------------------------------------------------------

interface PackCardProps {
  pack: KnowledgePackSummary
  onInstall: (pack: KnowledgePackSummary) => void
  isInstalling: boolean
  isInstallingThisPack: boolean
  /** Registry (or local) install state — renders a disabled "Installed ✓" card. */
  installed: boolean
}

function PackCard({ pack, onInstall, isInstalling, isInstallingThisPack, installed }: PackCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3 transition-colors",
        isInstallingThisPack && "border-brand/40 bg-brand/5",
      )}
      role="article"
      aria-label={`${pack.name} knowledge pack`}
    >
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <BookOpen className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
          <span className="truncate text-xs font-medium">{pack.name}</span>
        </div>
        <Badge variant="secondary" className="shrink-0 text-label-xxs">
          {pack.domain}
        </Badge>
      </div>

      <p className="mb-2 text-label-sm leading-relaxed text-muted-foreground line-clamp-2">
        {pack.description}
      </p>

      <div className="mb-2.5 flex items-center gap-3 text-label-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Database className="h-3 w-3" aria-hidden="true" />
          <span aria-label={`${pack.artifact_count} articles`}>
            {pack.artifact_count.toLocaleString()} articles
          </span>
        </span>
        <span className="flex items-center gap-1">
          <FileText className="h-3 w-3" aria-hidden="true" />
          <span aria-label={`Size: ${formatBytes(pack.size_bytes)}`}>
            {formatBytes(pack.size_bytes)}
          </span>
        </span>
        <span className="flex items-center gap-1">
          <Scale className="h-3 w-3" aria-hidden="true" />
          <span aria-label={`License: ${pack.license}`}>
            {formatLicense(pack.license)}
          </span>
        </span>
      </div>

      {installed ? (
        <Button
          size="sm"
          variant="outline"
          className="h-7 w-full text-xs"
          disabled
          aria-label={`${pack.name} already installed`}
        >
          Installed ✓
        </Button>
      ) : (
        <Button
          size="sm"
          className="h-7 w-full text-xs"
          onClick={() => onInstall(pack)}
          disabled={isInstalling || isInstallingThisPack}
          aria-label={`Install ${pack.name}`}
          aria-busy={isInstallingThisPack}
        >
          {isInstallingThisPack ? (
            <>
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" aria-hidden="true" />
              Installing...
            </>
          ) : (
            "Install"
          )}
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface SamplePackTabProps {
  /**
   * Called when the user has installed a pack and clicked "Continue to chat".
   * Receives the pack_id and the pack's article count so the wizard can mark
   * `firstDoc.ingested = true` AND populate the Review / Mode "X documents"
   * summary correctly (instead of the stale "0 documents" we used to show).
   */
  onComplete: (packId: string, articleCount: number) => void
}

export function SamplePackTab({ onComplete }: SamplePackTabProps) {
  const [selectedPack, setSelectedPack] = useState<KnowledgePackSummary | null>(null)

  const { install, isPending, isSuccess, error, installedPackId, reset } = useWizardPackInstall()

  // Fetch the catalog; re-use the same cache key as the KB-admin pane.
  // While the registry reports an in-flight install (this tab's or another
  // surface's), poll ~2s so the card flags track the async job.
  const { data: registry, isLoading: catalogLoading, isError: catalogError } = useQuery({
    queryKey: ["knowledge-pack-registry"],
    queryFn: fetchKnowledgePackRegistry,
    staleTime: 60_000,
    refetchInterval: (query) => (registryHasInstalling(query.state.data) ? 2000 : false),
  })

  // Flatten all packs from all domains and filter to featured set.
  const allPacks: KnowledgePackSummary[] = registry
    ? Object.values(registry.packs_by_domain).flat()
    : []
  const featuredPacks = FEATURED_PACK_IDS
    .map((id) => allPacks.find((p) => p.id === id))
    .filter((p): p is KnowledgePackSummary => p !== undefined)

  const handleInstall = useCallback(async (pack: KnowledgePackSummary) => {
    setSelectedPack(pack)
    reset()
    // install() rethrows on failure (incl. poll timeout); catch here so the
    // error surfaces through the hook's `error` state instead of propagating
    // as an unhandled rejection in the component tree.
    try {
      await install(pack.id)
    } catch {
      // error is available via the `error` field returned by useWizardPackInstall
    }
  }, [install, reset])

  const handleComplete = useCallback(() => {
    const id = installedPackId ?? selectedPack?.id
    if (id) onComplete(id, selectedPack?.artifact_count ?? 0)
  }, [installedPackId, selectedPack, onComplete])

  // Show demo panel once install succeeded
  if (isSuccess && selectedPack) {
    return (
      <DemoQueriesPanel
        pack={selectedPack}
        onComplete={handleComplete}
      />
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-center text-xs text-muted-foreground">
        Install a curated knowledge pack to explore Cerid with real content —
        no upload needed.
      </p>

      {/* Loading skeleton */}
      {catalogLoading && (
        <div
          className="space-y-2"
          role="status"
          aria-live="polite"
          aria-label="Loading knowledge pack catalog"
        >
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
          <span className="sr-only">Loading catalog...</span>
        </div>
      )}

      {/* Catalog error */}
      {catalogError && (
        <Alert variant="destructive" role="alert" aria-live="assertive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription className="text-xs">
            Could not load the pack catalog. Check that the Cerid backend is
            running, then refresh the wizard.
          </AlertDescription>
        </Alert>
      )}

      {/* Install error */}
      {error && (
        <Alert variant="destructive" role="alert" aria-live="assertive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription className="text-xs">
            {error.message ?? "Install failed — check the backend logs."}
          </AlertDescription>
        </Alert>
      )}

      {/* Pack cards */}
      {!catalogLoading && !catalogError && (
        <div
          className="space-y-2"
          aria-label="Available sample knowledge packs"
        >
          {featuredPacks.length === 0 ? (
            <p className="text-center text-xs text-muted-foreground">
              No featured packs found in the catalog. Check your registry.
            </p>
          ) : (
            featuredPacks.map((pack) => {
              const flags = packInstallFlags(pack)
              return (
                <PackCard
                  key={pack.id}
                  pack={pack}
                  onInstall={handleInstall}
                  isInstalling={isPending}
                  isInstallingThisPack={
                    (isPending && selectedPack?.id === pack.id) || flags.installing
                  }
                  installed={flags.installed}
                />
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
