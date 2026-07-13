// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * F3 — Source-add wizard. Three-step dialog: pick kind → configure →
 * test connection. Each step persists state so backing out doesn't
 * lose progress. On success, closes itself and emits the new
 * SourceRecord upstream for the FE store to react to.
 *
 * Brand surface: a plain Radix Dialog (NOT Liquid Glass; reserved
 * for hero surfaces). Connection-time metric pulses on completion
 * using .metric-pulse — that's the gamification beat.
 *
 * Configure UIs ship for rss, url_watch, webhook. Other kinds fall
 * back to a generic placeholder + default settings; their connectors
 * accept the empty config.
 */

import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, ChevronRight, Loader2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  createSource,
  listSourceKinds,
  type SourceRecord,
} from "@/lib/api/sources"
import type { SourceKindMetaExt } from "./source-kind-meta"
import { descriptorFor } from "./source-kind-icons"
import { WebhookShareCard } from "./webhook-share-card"
import { KindSpecificFields } from "./source-config-form"

type WizardStep = "pick" | "configure" | "result"

interface SourceAddWizardProps {
  open: boolean
  /** Pre-select a family or specific kind when opened. Optional. */
  initialFamily?: string
  initialKind?: string
  onClose: () => void
  onCreated?: (record: SourceRecord) => void
}

/**
 * Outer wrapper: keys the inner stateful body off the open prop so
 * every (re)open mounts a fresh wizard. Avoids the
 * setState-in-useEffect cascading-render pattern.
 */
export function SourceAddWizard(props: SourceAddWizardProps) {
  if (!props.open) {
    return (
      <Dialog open={false} onOpenChange={(v) => (!v ? props.onClose() : undefined)}>
        {/* Render nothing when closed; the Dialog primitive handles
            its own mount/unmount. */}
      </Dialog>
    )
  }
  // The remount-on-open key wipes all inner state cleanly without
  // an effect.
  return <SourceAddWizardInner key={`${props.initialKind ?? ""}-${props.initialFamily ?? ""}`} {...props} />
}

function SourceAddWizardInner({
  open,
  initialFamily,
  initialKind,
  onClose,
  onCreated,
}: SourceAddWizardProps) {
  const [step, setStep] = useState<WizardStep>(initialKind ? "configure" : "pick")
  const [kind, setKind] = useState<string | null>(initialKind ?? null)
  const [displayName, setDisplayName] = useState<string>("")
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [created, setCreated] = useState<SourceRecord | null>(null)

  const queryClient = useQueryClient()

  const { data: kinds } = useQuery<SourceKindMetaExt[]>({
    queryKey: ["source-kinds"],
    queryFn: listSourceKinds,
    staleTime: 60_000,
    enabled: open,
  })

  const filteredKinds = useMemo(() => {
    if (!kinds) return []
    if (initialFamily) return kinds.filter((k) => k.family === initialFamily)
    return kinds
  }, [kinds, initialFamily])

  const selectedMeta = useMemo(
    () => kinds?.find((k) => k.kind === kind),
    [kinds, kind],
  )

  // Recipe providers for the selected webhook-backed kind (chat_capture /
  // dev_events). Empty for kinds with no provider choice.
  const providers = selectedMeta?.providers ?? []
  // Folder kind: container-side roots a watched-folder path must live under.
  const allowedRoots = selectedMeta?.allowed_roots ?? []

  const createMut = useMutation({
    mutationFn: () => {
      if (!kind) throw new Error("kind required")
      return createSource({
        kind,
        display_name: displayName || descriptorFor(kind).label,
        config,
      })
    },
    onSuccess: (rec) => {
      setCreated(rec)
      setStep("result")
      queryClient.invalidateQueries({ queryKey: ["sources"] })
      queryClient.invalidateQueries({ queryKey: ["knowledge-stats"] })
      onCreated?.(rec)
    },
  })

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a source</DialogTitle>
          <DialogDescription>
            {step === "pick" && "Pick a source kind to begin"}
            {step === "configure" && kind && `Configure your ${descriptorFor(kind).label}`}
            {step === "result" && (created ? "Connection complete" : "")}
          </DialogDescription>
        </DialogHeader>

        {step === "pick" && (
          <PickStep
            kinds={filteredKinds}
            onPick={(k) => {
              setKind(k)
              setStep("configure")
            }}
          />
        )}

        {step === "configure" && kind && (
          <ConfigureStep
            kind={kind}
            providers={providers}
            allowedRoots={allowedRoots}
            displayName={displayName}
            onDisplayName={setDisplayName}
            config={config}
            onConfig={setConfig}
            onBack={() => setStep("pick")}
            onSubmit={() => createMut.mutate()}
            isSubmitting={createMut.isPending}
            error={createMut.error?.message ?? null}
          />
        )}

        {step === "result" && created && (
          <ResultStep record={created} onClose={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — pick kind
// ---------------------------------------------------------------------------

function PickStep({
  kinds,
  onPick,
}: {
  kinds: SourceKindMetaExt[]
  onPick: (kind: string) => void
}) {
  return (
    <div className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
      {kinds.map((k) => {
        const desc = descriptorFor(k.kind)
        const Icon = desc.icon
        const availability = k.availability ?? "available"
        const selectable = availability === "available"
        const prefix =
          availability === "coming_soon"
            ? "Soon · "
            : availability === "oauth"
              ? "Settings · "
              : availability === "requires_desktop"
                ? "Desktop app · "
                : k.tier === "pro"
                  ? "Pro · "
                  : ""
        return (
          <button
            key={k.kind}
            type="button"
            onClick={() => onPick(k.kind)}
            disabled={!selectable}
            className={cn(
              "flex flex-col items-start gap-1 rounded-lg border border-border/60 bg-card/40 px-3 py-2 text-left",
              selectable
                ? "cerid-press hover:border-border hover:bg-card/70"
                : "cursor-not-allowed opacity-55",
            )}
            aria-label={
              selectable
                ? `Add ${desc.label}`
                : availability === "oauth"
                  ? `${desc.label} — connect in Settings`
                  : availability === "requires_desktop"
                    ? `${desc.label} — requires the Cerid desktop app`
                    : `${desc.label} — coming soon`
            }
          >
            <Icon className="h-4 w-4 text-foreground/70" aria-hidden="true" />
            <span className="text-sm font-medium">{desc.label}</span>
            <span className="text-label-xs text-muted-foreground">
              {prefix}
              {desc.blurb}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — configure (per-kind)
// ---------------------------------------------------------------------------

function ConfigureStep({
  kind,
  providers,
  allowedRoots,
  displayName,
  onDisplayName,
  config,
  onConfig,
  onBack,
  onSubmit,
  isSubmitting,
  error,
}: {
  kind: string
  providers: string[]
  allowedRoots: string[]
  displayName: string
  onDisplayName: (v: string) => void
  config: Record<string, unknown>
  onConfig: (v: Record<string, unknown>) => void
  onBack: () => void
  onSubmit: () => void
  isSubmitting: boolean
  error: string | null
}) {
  // Webhook-backed typed kinds require a provider; the backend 422s without
  // one, so gate the Connect button until it's picked. Folder sources
  // likewise 422 without a path — gate until one is typed.
  const providerMissing = providers.length > 0 && !config.provider
  const pathMissing = kind === "folder" && !String(config.path ?? "").trim()
  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-medium text-foreground" htmlFor="display-name">
          Display name
        </label>
        <input
          id="display-name"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder={descriptorFor(kind).label}
          value={displayName}
          onChange={(e) => onDisplayName(e.target.value)}
        />
      </div>

      <KindSpecificFields
        kind={kind}
        providers={providers}
        allowedRoots={allowedRoots}
        config={config}
        onConfig={onConfig}
      />

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <Button variant="ghost" onClick={onBack} disabled={isSubmitting}>
          Back
        </Button>
        <Button onClick={onSubmit} disabled={isSubmitting || providerMissing || pathMissing}>
          {isSubmitting ? (
            <>
              <Loader2 className="mr-2 h-3 w-3 animate-spin" aria-hidden="true" />
              Connecting…
            </>
          ) : (
            <>
              Connect
              <ChevronRight className="ml-1 h-3 w-3" aria-hidden="true" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — result
// ---------------------------------------------------------------------------

function ResultStep({
  record,
  onClose,
}: {
  record: SourceRecord
  onClose: () => void
}) {
  const desc = descriptorFor(record.kind)
  const Icon = desc.icon
  const elapsed = record.connection_time_ms ?? 0

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 rounded-lg border border-border bg-card/40 px-4 py-3">
        <Icon className="h-6 w-6 text-foreground/80" />
        <div className="flex-1">
          <div className="text-sm font-medium">{record.display_name}</div>
          <div className="text-label-xs text-muted-foreground">{desc.label}</div>
        </div>
        <CheckCircle2 className="h-5 w-5 text-emerald-500" />
      </div>

      <div className="flex items-baseline justify-center gap-2 py-3">
        <span
          key={elapsed}
          className={cn(
            "metric-value-pulse text-3xl font-medium tabular-nums text-foreground",
          )}
        >
          {elapsed}
        </span>
        <span className="text-sm text-muted-foreground">ms to connect</span>
      </div>

      {record.family === "webhook" && (
        <WebhookShareCard sourceId={record.id} />
      )}

      <div className="flex justify-end pt-1">
        <Button onClick={onClose}>Done</Button>
      </div>
    </div>
  )
}

