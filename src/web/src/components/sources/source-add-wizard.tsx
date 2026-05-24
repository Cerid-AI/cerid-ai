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
 * Phase 2B ships configure UIs for: rss, url_watch, webhook.
 * Other kinds render a "coming soon" placeholder; the wizard still
 * lands them via the (future) connector module.
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
  type SourceKindMeta,
  type SourceRecord,
} from "@/lib/api/sources"
import { descriptorFor } from "./source-kind-icons"
import { WebhookShareCard } from "./webhook-share-card"

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

  const { data: kinds } = useQuery<SourceKindMeta[]>({
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
  kinds: SourceKindMeta[]
  onPick: (kind: string) => void
}) {
  return (
    <div className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
      {kinds.map((k) => {
        const desc = descriptorFor(k.kind)
        const Icon = desc.icon
        return (
          <button
            key={k.kind}
            type="button"
            onClick={() => onPick(k.kind)}
            className="cerid-press flex flex-col items-start gap-1 rounded-lg border border-border/60 bg-card/40 px-3 py-2 text-left hover:border-border hover:bg-card/70"
          >
            <Icon className="h-4 w-4 text-foreground/70" aria-hidden="true" />
            <span className="text-sm font-medium">{desc.label}</span>
            <span className="text-label-xs text-muted-foreground">
              {k.tier === "pro" ? "Pro · " : ""}
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
  displayName: string
  onDisplayName: (v: string) => void
  config: Record<string, unknown>
  onConfig: (v: Record<string, unknown>) => void
  onBack: () => void
  onSubmit: () => void
  isSubmitting: boolean
  error: string | null
}) {
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

      <KindSpecificFields kind={kind} config={config} onConfig={onConfig} />

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <Button variant="ghost" onClick={onBack} disabled={isSubmitting}>
          Back
        </Button>
        <Button onClick={onSubmit} disabled={isSubmitting}>
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

function KindSpecificFields({
  kind,
  config,
  onConfig,
}: {
  kind: string
  config: Record<string, unknown>
  onConfig: (v: Record<string, unknown>) => void
}) {
  if (kind === "rss" || kind === "url_watch") {
    return (
      <div>
        <label className="text-xs font-medium text-foreground" htmlFor="url">
          Feed URL
        </label>
        <input
          id="url"
          type="url"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="https://example.com/feed.xml"
          value={String(config.url ?? "")}
          onChange={(e) => onConfig({ ...config, url: e.target.value })}
        />
      </div>
    )
  }

  if (kind === "webhook") {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          A unique token is minted automatically. The receiver URL appears
          after the source is created.
        </p>
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={Boolean(config.require_hmac)}
            onChange={(e) =>
              onConfig({ ...config, require_hmac: e.target.checked })
            }
          />
          Require HMAC signature on inbound requests
        </label>
      </div>
    )
  }

  return (
    <div className="rounded-md border border-dashed border-border/60 px-3 py-3 text-xs text-muted-foreground">
      Configuration for this source kind ships in a follow-up phase. The
      source will be created with default settings.
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

      {record.kind === "webhook" && (
        <WebhookShareCard sourceId={record.id} />
      )}

      <div className="flex justify-end pt-1">
        <Button onClick={onClose}>Done</Button>
      </div>
    </div>
  )
}

