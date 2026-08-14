// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
 *
 * The pick step also offers a Notion export (RA-41): a one-shot zip-upload
 * migration, not a persistent connector kind, so it runs its own
 * upload + job-status-polling step against /api/migrate/notion instead of
 * createSource/ResultStep.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, ChevronRight, Loader2, Lock, Notebook, Upload } from "lucide-react"
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
import {
  migrateNotionExport,
  fetchMigrationStatus,
  type MigrationStatusResponse,
} from "@/lib/api/migration"
import type { SourceKindMetaExt } from "./source-kind-meta"
import { descriptorFor } from "./source-kind-icons"
import { WebhookShareCard } from "./webhook-share-card"
import { KindSpecificFields } from "./source-config-form"
import { useEntitlements } from "@/hooks/use-entitlements"
import { EntitlementsUnavailableNote } from "@/components/shared/entitlements-error-notice"
import { ProUpgradeOverlay } from "./pro-upgrade-overlay"
import { ProgressBar } from "@/components/ui/progress-bar"

type WizardStep = "pick" | "configure" | "result" | "migrate-notion"

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
  // Kind whose Pro gate the user just walked into — drives the upgrade dialog,
  // same shape as SourcesConnectors.
  const [upgradeKind, setUpgradeKind] = useState<string | null>(null)

  const queryClient = useQueryClient()

  // This wizard is reachable from the AddSourceFab (⌘⇧S) without passing
  // through SourcesConnectors or the empty gallery, so it needs its own gate:
  // before this it let a community user configure a Pro kind all the way to
  // Connect and then surfaced the server's 403 as a raw error string.
  //
  // Resolution is per FLAG: /sources/kinds carries the FEATURE_FLAGS key
  // gating each kind (`feature_flag`), so the verdict comes from the server's
  // own flag detail — "locked" is the only state an upgrade fixes; an
  // entitled tier whose flag is off takes the normal route and the server
  // answers. The second argument to forFlag is the registry-tier fallback
  // and is what makes an unresolvable entitlement (flag missing, capabilities
  // fetch failed) fail CLOSED — omitting it returns "available".
  // `isLoading` suppresses the verdict because `tier` defaults to "community"
  // while capabilities are in flight, and a paying customer must not be shown
  // an upgrade pitch on first paint. The server stays the enforcement point
  // (POST /sources 403s for Pro kinds at community tier); this only keeps the
  // wizard from walking someone into that dead end.
  const { forFlag, isLoading: entitlementsLoading, isError: entitlementsError } = useEntitlements()
  const isKindLocked = (meta?: SourceKindMetaExt) =>
    !!meta &&
    meta.tier === "pro" &&
    !entitlementsLoading &&
    forFlag(meta.feature_flag ?? undefined, "pro").state === "locked"

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

  // A pre-selected kind (FAB deep-link, gallery hand-off) skips the pick step,
  // so the lock has to be re-checked here too — otherwise the one entry point
  // that never renders a tile is also the one with no gate.
  const selectionLocked = isKindLocked(selectedMeta)

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a source</DialogTitle>
          <DialogDescription>
            {step === "pick" && "Pick a source kind to begin"}
            {step === "configure" && kind && `Configure your ${descriptorFor(kind).label}`}
            {step === "result" && (created ? "Connection complete" : "")}
            {step === "migrate-notion" && "Import a Notion export"}
          </DialogDescription>
        </DialogHeader>

        {entitlementsError && step === "pick" && <EntitlementsUnavailableNote />}

        {step === "pick" && (
          <PickStep
            kinds={filteredKinds}
            isLocked={(k) => isKindLocked(filteredKinds.find((m) => m.kind === k))}
            onPick={(k) => {
              // Locked rows stay visible and clickable — they are the funnel —
              // but route to the upgrade dialog instead of the configure step.
              if (isKindLocked(filteredKinds.find((m) => m.kind === k))) {
                setUpgradeKind(k)
                return
              }
              setKind(k)
              setStep("configure")
            }}
            onPickNotionMigration={initialFamily ? undefined : () => setStep("migrate-notion")}
          />
        )}

        {step === "configure" && kind && selectionLocked && entitlementsError && (
          <div className="space-y-3">
            <EntitlementsUnavailableNote />
            <Button variant="outline" onClick={() => setStep("pick")} className="cerid-press">
              Back
            </Button>
          </div>
        )}

        {step === "configure" && kind && selectionLocked && !entitlementsError && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {descriptorFor(kind).label} is part of Cerid Pro.
            </p>
            <Button onClick={() => setUpgradeKind(kind)} className="cerid-press">
              <Lock className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Unlock with Pro
            </Button>
          </div>
        )}

        {step === "configure" && kind && !selectionLocked && (
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

        {step === "migrate-notion" && (
          <NotionMigrationStep onBack={() => setStep("pick")} onDone={onClose} />
        )}

        <ProUpgradeOverlay
          open={upgradeKind !== null}
          kind={upgradeKind}
          onClose={() => setUpgradeKind(null)}
        />
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — pick kind
// ---------------------------------------------------------------------------

function PickStep({
  kinds,
  isLocked,
  onPick,
  onPickNotionMigration,
}: {
  kinds: SourceKindMetaExt[]
  /** Pro-tier kind this plan can't use. Required, not defaulted: a call site
      that forgets it should fail the build rather than ship the grid ungated. */
  isLocked: (kind: string) => boolean
  onPick: (kind: string) => void
  /** RA-41: a one-shot zip-import migration, not a persistent connector kind
      — not part of the server's /sources/kinds registry, so it isn't in
      `kinds`. Undefined when the wizard was opened scoped to one family
      (deep-link / gallery hand-off), matching how locked tiles behave. */
  onPickNotionMigration?: () => void
}) {
  return (
    <div className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
      {onPickNotionMigration && (
        <button
          type="button"
          onClick={onPickNotionMigration}
          className="cerid-press flex flex-col items-start gap-1 rounded-lg border border-border/60 bg-card/40 px-3 py-2 text-left hover:border-border hover:bg-card/70"
          aria-label="Import a Notion export"
        >
          <Notebook className="h-4 w-4 text-foreground/70" aria-hidden="true" />
          <span className="text-sm font-medium">Notion export</span>
          <span className="text-label-xs text-muted-foreground">One-shot import from a Notion ZIP export</span>
        </button>
      )}
      {kinds.map((k) => {
        const desc = descriptorFor(k.kind)
        const Icon = desc.icon
        const availability = k.availability ?? "available"
        const locked = isLocked(k.kind)
        // A locked tile stays clickable — it opens the upgrade dialog. Greying
        // it out would hide the thing we're selling.
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
                ? locked
                  ? `${desc.label} — requires Cerid Pro`
                  : `Add ${desc.label}`
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

// ---------------------------------------------------------------------------
// Notion migration — zip upload + job-status polling (RA-41)
//
// Not a persistent connector: POST /api/migrate/notion parses the export and
// runs a background ingest job, tracked by GET /api/migrate/status/{job_id}.
// There is no resulting SourceRecord, so this bypasses createSource/ResultStep
// entirely and reports progress from the migration job itself.
// ---------------------------------------------------------------------------

const MIGRATION_POLL_MS = 1500

function NotionMigrationStep({
  onBack,
  onDone,
}: {
  onBack: () => void
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<MigrationStatusResponse | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const startMut = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("Choose a .zip export first")
      return migrateNotionExport(file)
    },
    onSuccess: (res) => setJobId(res.job_id),
  })

  useEffect(() => {
    if (!jobId) return
    const poll = async () => {
      try {
        const s = await fetchMigrationStatus(jobId)
        setStatus(s)
        setPollError(null)
        if (s.status === "completed") {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
          queryClient.invalidateQueries({ queryKey: ["sources"] })
          queryClient.invalidateQueries({ queryKey: ["knowledge-stats"] })
        }
      } catch (e) {
        setPollError(e instanceof Error ? e.message : "Status check failed")
      }
    }
    void poll()
    pollRef.current = setInterval(poll, MIGRATION_POLL_MS)
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  }, [jobId, queryClient])

  const inFlight = jobId !== null && status?.status !== "completed"
  const pct = status && status.total > 0 ? Math.round((status.processed / status.total) * 100) : 0

  return (
    <div className="space-y-4">
      {!jobId && (
        <>
          <div>
            <label className="text-xs font-medium text-foreground" htmlFor="notion-zip">
              Notion export (.zip)
            </label>
            <input
              id="notion-zip"
              type="file"
              accept=".zip"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mt-1 w-full text-sm"
            />
            <p className="mt-1 text-label-xs text-muted-foreground">
              Export your workspace from Notion as Markdown &amp; CSV, then upload the ZIP here.
            </p>
          </div>

          {startMut.error && (
            <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {startMut.error instanceof Error ? startMut.error.message : "Import failed"}
            </div>
          )}

          <div className="flex items-center justify-between pt-2">
            <Button variant="ghost" onClick={onBack} disabled={startMut.isPending}>
              Back
            </Button>
            <Button onClick={() => startMut.mutate()} disabled={!file || startMut.isPending}>
              {startMut.isPending ? (
                <>
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" aria-hidden="true" />
                  Uploading…
                </>
              ) : (
                <>
                  <Upload className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  Import
                </>
              )}
            </Button>
          </div>
        </>
      )}

      {jobId && (
        <div className="space-y-3">
          <div className="flex items-center gap-3 rounded-lg border border-border bg-card/40 px-4 py-3">
            <Notebook className="h-6 w-6 text-foreground/80" />
            <div className="flex-1">
              <div className="text-sm font-medium">
                {status?.status === "completed" ? "Import complete" : "Importing…"}
              </div>
              <div className="text-label-xs text-muted-foreground">
                {status ? `${status.processed}/${status.total} pages` : "Starting…"}
                {status && status.errors > 0 && ` · ${status.errors} error${status.errors === 1 ? "" : "s"}`}
              </div>
            </div>
            {status?.status === "completed" ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-500" />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            )}
          </div>

          {status && status.total > 0 && (
            <ProgressBar pct={pct} size="sm" label="Notion import progress" fillClassName="bg-brand" />
          )}

          {pollError && (
            <p role="alert" className="text-label-xs text-destructive">{pollError}</p>
          )}

          <div className="flex justify-end pt-1">
            <Button onClick={onDone} disabled={inFlight}>Done</Button>
          </div>
        </div>
      )}
    </div>
  )
}

