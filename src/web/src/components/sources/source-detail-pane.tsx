// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Source-detail pane (one card per source, four sections).
 *
 *   - Header — Liquid Glass: icon, display name, status pill, connection-time
 *   - Activity — streaming feed (SSE wire-in consumes /observability/source-activity)
 *   - Health — sync cursor position, last-sync timestamp, error badge
 *   - Policy — retention slider, quality-floor slider, disconnect button
 *
 * Both sliders commit on release (no per-tick API calls). Commit
 * pulses the total-artifacts counter via .metric-pulse to confirm.
 *
 * Brand surfaces:
 *   - Liquid Glass: header card only
 *   - .cerid-press: sliders + buttons
 *   - .metric-value-pulse: total-artifacts counter on policy commit
 */

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, CheckCircle2, Trash2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  deleteSource,
  patchSourcePolicy,
  testSource,
  type SourceRecord,
} from "@/lib/api/sources"
import { descriptorFor } from "./source-kind-icons"
import { SourceConfigForm, EDITABLE_CONFIG_KINDS } from "./source-config-form"

type RetentionMode = "keep_all" | "days" | "count"

interface SourceDetailPaneProps {
  open: boolean
  source: SourceRecord | null
  onClose: () => void
  onDeleted?: (sourceId: string) => void
}

export function SourceDetailPane({
  open,
  source,
  onClose,
  onDeleted,
}: SourceDetailPaneProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl p-0">
        {open && source && (
          <SourceDetailInner
            key={source.id}
            source={source}
            onClose={onClose}
            onDeleted={onDeleted}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function SourceDetailInner({
  source,
  onClose,
  onDeleted,
}: {
  source: SourceRecord
  onClose: () => void
  onDeleted?: (id: string) => void
}) {
  const desc = descriptorFor(source.kind)
  const Icon = desc.icon

  const queryClient = useQueryClient()
  const [retentionMode, setRetentionMode] = useState<RetentionMode>(
    (source.sync_cursor && typeof source.sync_cursor === "object" && "imported" in source.sync_cursor)
      ? "keep_all"
      : retentionFromSource(source),
  )
  const [retentionDays, setRetentionDays] = useState<number>(
    retentionDaysFromSource(source),
  )
  const [retentionMax, setRetentionMax] = useState<number>(
    retentionMaxFromSource(source),
  )
  // Seed from the persisted floor so "Apply policy" doesn't silently reset a
  // non-zero floor to 0 (the slider's previous hard-coded initial value).
  const [qualityFloor, setQualityFloor] = useState<number>(source.quality_floor ?? 0)
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  const patchMut = useMutation({
    mutationFn: () => {
      const policy =
        retentionMode === "keep_all"
          ? { mode: "keep_all" as const }
          : retentionMode === "days"
            ? { mode: "days" as const, days: retentionDays }
            : { mode: "count" as const, max: retentionMax }
      return patchSourcePolicy(source.id, {
        retention_policy: policy,
        quality_floor: qualityFloor,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingestion-sources"] })
    },
  })

  const deleteMut = useMutation({
    mutationFn: () => deleteSource(source.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingestion-sources"] })
      onDeleted?.(source.id)
      onClose()
    },
  })

  const testMut = useMutation({
    mutationFn: () => testSource(source.id),
    onSuccess: (r) => setTestResult({ ok: r.ok, detail: r.detail }),
  })

  return (
    <div className="space-y-0">
      {/* Header — Liquid Glass */}
      <DialogHeader className="liquid-glass rounded-t-lg px-5 py-4">
        <div className="flex items-center gap-3">
          <Icon className="h-7 w-7 text-foreground/80" />
          <div className="flex-1">
            <DialogTitle className="text-base font-medium">
              {source.display_name}
            </DialogTitle>
            <div className="mt-0.5 flex items-center gap-2 text-label-xs text-muted-foreground">
              <span>{desc.label}</span>
              <StatusPill status={source.status} />
              {typeof source.connection_time_ms === "number" && (
                <span>· connected in {source.connection_time_ms} ms</span>
              )}
            </div>
          </div>
        </div>
      </DialogHeader>

      <div className="space-y-5 px-5 py-4">
        {/* Activity */}
        <Section title="Activity">
          <div className="grid grid-cols-3 gap-3 rounded-md border border-border bg-card/30 px-3 py-2 text-center">
            <Stat label="artifacts" value={source.total_artifacts} />
            <Stat label="chunks" value={source.total_chunks} />
            <Stat label="24h" value={source.total_artifacts_24h} />
          </div>
        </Section>

        {/* Health */}
        <Section title="Health">
          <div className="flex items-center gap-2 text-sm">
            <Button
              size="sm"
              variant="outline"
              onClick={() => testMut.mutate()}
              disabled={testMut.isPending}
              className="cerid-press"
            >
              Run health check
            </Button>
            {testResult && (
              <span
                className={cn(
                  "inline-flex items-center gap-1 text-label-xs",
                  testResult.ok ? "text-emerald-500" : "text-amber-500",
                )}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="h-3 w-3" />
                ) : (
                  <AlertTriangle className="h-3 w-3" />
                )}
                {testResult.detail}
              </span>
            )}
          </div>
          {source.last_error && (
            <p className="mt-1 text-label-xs text-destructive">{source.last_error}</p>
          )}
          {source.last_sync_at && (
            <p className="mt-1 text-label-xs text-muted-foreground">
              Last sync: {new Date(source.last_sync_at).toLocaleString()}
            </p>
          )}
        </Section>

        {/* Configuration — inline edit form for data-source kinds (gated to editable kinds) */}
        {EDITABLE_CONFIG_KINDS.includes(source.kind as typeof EDITABLE_CONFIG_KINDS[number]) && (
          <Section title="Configuration">
            <SourceConfigForm
              source={source}
              onSaved={() => { /* invalidation handled by SourceConfigForm.saveMut.onSuccess */ }}
            />
          </Section>
        )}

        {/* Policy — retention + quality floor (not applicable for folder sources) */}
        <Section title="Policy">
          {source.kind === "folder" ? (
            <p className="text-label-xs text-muted-foreground">
              Retention and quality-floor settings aren&apos;t available for watched folders yet.
            </p>
          ) : (
            <div className="space-y-3">
              <RetentionPicker
                mode={retentionMode}
                days={retentionDays}
                max={retentionMax}
                onMode={setRetentionMode}
                onDays={setRetentionDays}
                onMax={setRetentionMax}
              />
              <FloorSlider value={qualityFloor} onChange={setQualityFloor} />
              <div className="flex justify-end gap-2 pt-1">
                <Button
                  size="sm"
                  onClick={() => patchMut.mutate()}
                  disabled={patchMut.isPending}
                  className="cerid-press"
                >
                  {patchMut.isPending ? "Saving…" : "Apply policy"}
                </Button>
              </div>
            </div>
          )}
        </Section>

        {/* Danger zone */}
        <Section title="Danger zone">
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setConfirmDisconnect(true)}
            disabled={deleteMut.isPending}
            className="cerid-press"
          >
            <Trash2 className="mr-1 h-3 w-3" />
            Disconnect source
          </Button>
        </Section>
      </div>

      <AlertDialog open={confirmDisconnect} onOpenChange={setConfirmDisconnect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect this source?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the source connection. Already-ingested content stays
              in your knowledge base, but no new items will be pulled.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                setConfirmDisconnect(false)
                deleteMut.mutate()
              }}
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-foreground">{title}</h3>
      {children}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div
        key={value}
        className="metric-value-pulse text-lg font-medium tabular-nums text-foreground"
      >
        {value.toLocaleString()}
      </div>
      <div className="text-label-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const color =
    status === "connected"
      ? "bg-emerald-500/10 text-emerald-500"
      : status === "error"
        ? "bg-destructive/10 text-destructive"
        : "bg-muted text-muted-foreground"
  return (
    <span className={cn("rounded-full px-1.5 py-0.5 text-label-xs", color)}>
      {status}
    </span>
  )
}

function RetentionPicker({
  mode,
  days,
  max,
  onMode,
  onDays,
  onMax,
}: {
  mode: RetentionMode
  days: number
  max: number
  onMode: (m: RetentionMode) => void
  onDays: (d: number) => void
  onMax: (m: number) => void
}) {
  return (
    <fieldset>
      <legend className="text-label-xs uppercase tracking-wide text-muted-foreground">
        Retention
      </legend>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {(["keep_all", "days", "count"] as RetentionMode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onMode(m)}
            className={cn(
              "cerid-press rounded-full border px-2.5 py-1 text-label-xs transition-colors",
              mode === m
                ? "border-foreground bg-foreground/10 text-foreground"
                : "border-border text-muted-foreground hover:border-foreground/40",
            )}
            aria-pressed={mode === m}
          >
            {m === "keep_all" ? "Keep all" : m === "days" ? "By age" : "By count"}
          </button>
        ))}
        {mode === "days" && (
          <input
            type="number"
            min={0}
            value={days}
            onChange={(e) => onDays(Number(e.target.value))}
            className="w-20 rounded-md border border-border bg-background px-2 py-1 text-xs"
            aria-label="Days to retain"
          />
        )}
        {mode === "count" && (
          <input
            type="number"
            min={0}
            value={max}
            onChange={(e) => onMax(Number(e.target.value))}
            className="w-20 rounded-md border border-border bg-background px-2 py-1 text-xs"
            aria-label="Max artifacts to keep"
          />
        )}
      </div>
      <p className="mt-1 text-label-xs text-muted-foreground">
        {mode === "keep_all"
          ? "Keep all is the default — nothing is purged until you switch to By age or By count and apply the policy."
          : "Applies nightly. Artifacts outside the limit are purged on the next retention pass."}
      </p>
    </fieldset>
  )
}

function FloorSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <label className="text-label-xs uppercase tracking-wide text-muted-foreground">
        Quality floor — drop artifacts below {(value * 100).toFixed(0)}%
      </label>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="cerid-press mt-1 w-full"
        aria-label="Quality floor"
      />
    </div>
  )
}

function retentionFromSource(src: SourceRecord): RetentionMode {
  const pol = src.sync_cursor as unknown
  if (pol && typeof pol === "object" && "mode" in pol) {
    const m = (pol as { mode: string }).mode
    if (m === "days" || m === "count" || m === "keep_all") return m
  }
  return "keep_all"
}

function retentionDaysFromSource(src: SourceRecord): number {
  const pol = src.sync_cursor as unknown
  if (pol && typeof pol === "object" && "days" in pol) {
    const d = (pol as { days: number }).days
    return Number(d) || 30
  }
  return 30
}

function retentionMaxFromSource(src: SourceRecord): number {
  const pol = src.sync_cursor as unknown
  if (pol && typeof pol === "object" && "max" in pol) {
    const m = (pol as { max: number }).max
    return Number(m) || 1000
  }
  return 1000
}
