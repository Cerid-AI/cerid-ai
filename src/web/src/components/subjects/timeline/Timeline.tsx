// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Subjects → Timeline — Stratigraph orchestrator (v2, Tephra Cycle-2).
//
// 4-state matrix: Skeleton loading / destructive Alert error /
// EmptyState empty / populated StratigraphCanvas.
// Chrome parity with Constellation map mode: lens radiogroup, type
// filter chips, config popover, pinned shelf (generalized lane|event),
// community card, entity pin card.
//
// Tephra Cycle-2 additions:
//   - Extended bucket-detail card: templated L2 sentences (amendment #4)
//   - Event card with composeChat seeds + wiki goTo
//   - since-you-last-looked lastViewedAt persistence (unmount write)
//   - Nearest-activity jump in empty-window state (amendment #3)
//   - Freeze/re-rank gated to community lens only (amendment #1)
//   - Pinned shelf generalized to {type: lane|event} (amendment #7)
//   - communitySlot hue fix at pinned shelf (amendment #7)
//   - 4-state + jest-axe on all panel surfaces (D.2 contract)
//
// recharts is intentionally absent from this file.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  AlertCircle,
  ArrowRight,
  CircleDashed,
  Clock,
  Layers,
  Settings2,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  TriangleAlert,
  X,
} from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { EmptyState } from "@/components/ui/empty-state"
import { InfoTip } from "@/components/ui/info-tip"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { resolveMapTokens, type MapTokens } from "@/components/subjects/constellation/map/community-layer"
import { useNavigation } from "@/contexts/navigation-context"
import { useTimelineStrata, useTimelineTrack } from "./stratigraph/use-timeline-strata"
import {
  loadTimelineConfig,
  saveTimelineConfig,
  stampLastViewed,
  type TimelineConfig,
  type TrackBudget,
  type PinnedItem,
  type PinnedEventItem,
} from "./stratigraph/timeline-config"
import type {
  StrataEvent,
  LaneMeta,
  TrackEventExtended,
  TimelineTrackExtension,
  SinceMarker,
} from "./stratigraph/strata-types"
import {
  StratigraphCanvas,
  type TimelineLens,
  type PinnedCommunity,
  type PinnedTrack,
} from "./stratigraph/StratigraphCanvas"
import { communitySlot } from "./stratigraph/strata-layout"

// ---------------------------------------------------------------------------
// Lens definitions (parity with Constellation)
// ---------------------------------------------------------------------------

const LENSES: { id: TimelineLens; label: string; hint: string }[] = [
  { id: "cluster", label: "Clusters", hint: "Color strata by knowledge community" },
  { id: "trust", label: "Trust", hint: "Severity-priority trust bands; unverified dominates" },
  { id: "type", label: "Types", hint: "Re-partition stack by entity type (no refetch)" },
  { id: "domain", label: "Domains", hint: "Re-partition stack by primary knowledge domain (8 lanes + labeled Other)" },
]

const PERIODS: { label: string; value: TimelineConfig["period"] }[] = [
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
  { label: "90d", value: "90d" },
  { label: "180d", value: "180d" },
  { label: "1y", value: "365d" },
]

// ---------------------------------------------------------------------------
// Config popover
// ---------------------------------------------------------------------------

function TimelineConfigPanel({
  config,
  onChange,
}: {
  config: TimelineConfig
  onChange: (patch: Partial<TimelineConfig>) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Timeline settings"
        aria-expanded={open}
        className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
      >
        <Settings2 className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute bottom-full right-0 mb-1 w-56 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
          <div className="flex flex-col gap-2">
            {/* Track budget */}
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Track budget</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Track budget">
                {([8, 16, 24] as TrackBudget[]).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.trackBudget === v}
                    onClick={() => onChange({ trackBudget: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs transition-colors ${
                      config.trackBudget === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Markers toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.markersVisible}
                onChange={(e) => onChange({ markersVisible: e.target.checked })}
                className="rounded border-border/60"
              />
              Show event markers
            </label>

            {/* Ingest hatch toggle */}
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.ingestHatch}
                onChange={(e) => onChange({ ingestHatch: e.target.checked })}
                className="rounded border-border/60"
              />
              Hatch ingest bursts
            </label>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Templated L2 sentence composer (amendment #4 — zero LLM, pure field composition)
// ---------------------------------------------------------------------------

interface BucketContext {
  laneMeta?: LaneMeta | null
  /** ISO-8601 bucket date */
  bucketDate: string
  /** Raw mention count for this (lane, bucket) */
  mentionCount: number
  events: StrataEvent[]
  verification?: import("./stratigraph/strata-types").VerificationAggregate | null
  topEntities: import("./stratigraph/strata-types").TopEntity[]
}

function formatBucketDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
  } catch {
    return iso
  }
}

/** Compose deterministic L2 sentences from payload fields. No LLM calls. */
function composeBucketSentences(ctx: BucketContext): string[] {
  const sentences: string[] = []
  const laneName = ctx.laneMeta?.label ?? "this lane"
  const dateLabel = formatBucketDate(ctx.bucketDate)

  // Birth sentence — new entities that appeared
  const births = ctx.events.filter((e) => e.kind === "enrich" && e.entity_name)
  if (births.length > 0) {
    const names = births.slice(0, 3).map((b) => b.entity_name!).join(", ")
    const extra = births.length > 3 ? ` and ${births.length - 3} more` : ""
    sentences.push(`${births.length === 1 ? "A new entity" : `${births.length} entities`} appeared in ${laneName} around ${dateLabel}: ${names}${extra}.`)
  }

  // Refresh / enrich excerpts sentence
  const refreshes = ctx.events.filter((e) => e.kind === "refresh" || e.kind === "enrich")
  if (refreshes.length > 0) {
    const lastWithSummary = refreshes.find((e) => e.summary)
    if (lastWithSummary?.summary) {
      sentences.push(`Recent activity: "${lastWithSummary.summary.slice(0, 120)}${lastWithSummary.summary.length > 120 ? "…" : ""}"`)
    } else {
      sentences.push(`${refreshes.length} knowledge update${refreshes.length > 1 ? "s" : ""} in ${laneName} around ${dateLabel}.`)
    }
  }

  // Verification ratio line — sparse suppression: suppress when report_count < 3 (amendment #2)
  if (ctx.verification && ctx.verification.report_count >= 3) {
    const { report_count, verified, unverified } = ctx.verification
    const verifiedPct = Math.round((verified / report_count) * 100)
    const unverifiedPct = Math.round((unverified / report_count) * 100)
    const rateLabel = verifiedPct >= 70 ? "mostly verified" : unverifiedPct >= 50 ? "frequently unverified" : "mixed verification"
    sentences.push(`Verification across ${report_count} report${report_count > 1 ? "s" : ""}: ${rateLabel} (${verifiedPct}% verified, ${unverifiedPct}% unverified).`)
  }

  // Burst attribution
  const burstEvents = ctx.events.filter((e) => e.is_spike)
  if (burstEvents.length > 0 || (ctx.verification?.is_spike)) {
    sentences.push(`Activity was elevated in ${laneName} around ${dateLabel} — ${ctx.mentionCount.toLocaleString()} mentions.`)
  }

  if (sentences.length === 0) {
    sentences.push(`${ctx.mentionCount.toLocaleString()} mention${ctx.mentionCount !== 1 ? "s" : ""} in ${laneName} around ${dateLabel}.`)
  }

  return sentences
}

// ---------------------------------------------------------------------------
// Verification trust icon (maps to existing canonical color scale)
// ---------------------------------------------------------------------------

function VerificationStateIcon({ score }: { score: number }) {
  if (score >= 0.8) return <ShieldCheck className="h-3 w-3 text-green-500" aria-hidden="true" />
  if (score >= 0.5) return <Shield className="h-3 w-3 text-yellow-500" aria-hidden="true" />
  if (score > 0) return <ShieldAlert className="h-3 w-3 text-amber-500" aria-hidden="true" />
  return <ShieldOff className="h-3 w-3 text-muted-foreground/50" aria-hidden="true" />
}

// ---------------------------------------------------------------------------
// Extended bucket-detail card (L2 click ladder, deliverable #1)
// ---------------------------------------------------------------------------

interface BucketDetailCardProps {
  bucketDate: string
  laneId: string
  laneMeta?: LaneMeta | null
  mentionCount: number
  events: StrataEvent[]
  verification?: import("./stratigraph/strata-types").VerificationAggregate | null
  topEntities: import("./stratigraph/strata-types").TopEntity[]
  communityLensActive: boolean
  onClose: () => void
  onEventClick: (event: StrataEvent) => void
}

function BucketDetailCard({
  bucketDate,
  laneId,
  laneMeta,
  mentionCount,
  events,
  verification,
  topEntities,
  communityLensActive,
  onClose,
  onEventClick,
}: BucketDetailCardProps) {
  const { composeChat, goTo } = useNavigation()

  const sentences = composeBucketSentences({
    laneMeta,
    bucketDate,
    mentionCount,
    events,
    verification: verification ?? null,
    topEntities,
  })

  const contradictionEvents = events.filter(
    (e) => e.kind === "contradiction_finding" || e.kind === "contradict",
  )

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="bucket-detail-card"
      data-lane-id={laneId}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-semibold text-foreground">
              {laneMeta?.label ?? "Activity detail"}
            </span>
            {/* Amendment #6: auto-label badge for unsummarized community lanes */}
            {communityLensActive && laneMeta?.is_auto_label && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center" aria-label="Auto-labelled lane">
                      <CircleDashed className="h-3 w-3 text-muted-foreground/60" aria-hidden="true" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="text-xs">
                    Label derived from top hub entity — community summary pending weekly run
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
          </div>
          <div className="mt-0.5 text-label-xs text-muted-foreground">
            {formatBucketDate(bucketDate)} · {mentionCount.toLocaleString()} mentions
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close detail card"
          className="rounded p-1 text-muted-foreground hover:bg-accent/40"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Templated sentences (amendment #4) */}
      <div className="flex flex-col gap-1.5">
        {sentences.map((sentence, i) => (
          <button
            key={i}
            type="button"
            className="rounded-md border border-border/40 bg-background/60 px-2.5 py-1.5 text-left text-label-xs text-foreground hover:bg-accent/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
            title="Ask about this"
            onClick={() => composeChat({ text: sentence })}
          >
            {sentence}
          </button>
        ))}
      </div>

      {/* Verification summary — sparse-suppressed below 3 reports */}
      {verification && verification.report_count >= 3 && (
        <div className="flex items-center gap-1.5 text-label-xs">
          <VerificationStateIcon score={verification.overall_score_avg} />
          <span className="text-muted-foreground">
            {verification.report_count} verification report{verification.report_count > 1 ? "s" : ""}
          </span>
          {verification.is_spike && (
            <Badge variant="outline" className="ml-0.5 border-amber-500/40 bg-amber-500/10 text-amber-600 text-label-xxs">
              spike
            </Badge>
          )}
        </div>
      )}

      {/* Community summary (community lens only) */}
      {communityLensActive && laneMeta?.summary_full && (
        <p className="rounded-md bg-muted/40 px-2.5 py-1.5 text-label-xs text-muted-foreground leading-relaxed">
          {laneMeta.summary_full}
        </p>
      )}

      {/* Top entities */}
      {topEntities.length > 0 && (
        <div>
          <div className="mb-1 text-label-xs font-medium text-muted-foreground">Top entities</div>
          <div className="flex flex-wrap gap-1">
            {topEntities.map((e) => (
              <button
                key={e.slug}
                type="button"
                onClick={() => goTo("subjects", { mode: "wiki", entity: e.slug })}
                className="rounded-full border border-border/60 bg-card/70 px-2 py-0.5 text-label-xs text-foreground hover:bg-accent/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              >
                {e.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Contradiction events */}
      {contradictionEvents.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-label-xs font-medium text-muted-foreground">
            <TriangleAlert className="h-3 w-3 text-amber-500" aria-hidden="true" />
            {contradictionEvents.length} contradiction{contradictionEvents.length > 1 ? "s" : ""}
          </div>
          <div className="flex flex-col gap-1">
            {contradictionEvents.map((ev, i) => (
              <button
                key={i}
                type="button"
                className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-left text-label-xs hover:bg-amber-500/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => onEventClick(ev)}
              >
                <span className="text-foreground">Contradiction detected</span>
                {ev.severity && (
                  <Badge variant="outline" className={`ml-1.5 text-label-xxs ${
                    ev.severity === "high"
                      ? "border-red-500/40 bg-red-500/10 text-red-600"
                      : "border-amber-500/40 bg-amber-500/10 text-amber-600"
                  }`}>
                    {ev.severity}
                  </Badge>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Zero contradictions quiet state */}
      {contradictionEvents.length === 0 && (
        <p className="text-label-xxs text-muted-foreground/50">No contradictions recorded in this window.</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Event detail card (L2 for event glyph clicks, deliverable #1)
// ---------------------------------------------------------------------------

interface EventDetailCardProps {
  event: StrataEvent
  onClose: () => void
}

function EventDetailCard({ event, onClose }: EventDetailCardProps) {
  const { composeChat, goTo } = useNavigation()

  const seedText = event.entity_name
    ? `Tell me about the ${event.kind === "contradiction_finding" ? "contradiction involving" : "knowledge event for"} ${event.entity_name}`
    : `Tell me about this knowledge event from ${event.ts.slice(0, 10)}`

  return (
    <div className="flex flex-col gap-3" data-testid="event-detail-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            {(event.kind === "contradiction_finding" || event.kind === "contradict") && (
              <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden="true" />
            )}
            <span className="text-sm font-semibold text-foreground">
              {event.entity_name ?? "Knowledge event"}
            </span>
            {event.severity && (
              <Badge variant="outline" className={`text-label-xxs ${
                event.severity === "high"
                  ? "border-red-500/40 bg-red-500/10 text-red-600"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600"
              }`}>
                {event.severity}
              </Badge>
            )}
          </div>
          <div className="mt-0.5 text-label-xs text-muted-foreground">
            {event.kind.replace("_", " ")} · {event.ts.slice(0, 10)}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close event card"
          className="rounded p-1 text-muted-foreground hover:bg-accent/40"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Summary */}
      {event.summary && (
        <p className="rounded-md bg-muted/40 px-2.5 py-1.5 text-label-xs text-muted-foreground leading-relaxed">
          {event.summary}
        </p>
      )}

      {/* Contradiction claim texts (denormalized — no fetch needed) */}
      {(event.claim_a || event.claim_b) && (
        <div className="flex flex-col gap-1">
          <div className="text-label-xs font-medium text-muted-foreground">Conflicting claims</div>
          {event.claim_a && (
            <div className="rounded-md border border-border/40 bg-background/60 px-2 py-1 text-label-xs text-foreground">
              {event.claim_a}
            </div>
          )}
          {event.claim_b && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-label-xs text-foreground">
              {event.claim_b}
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-col gap-1">
        <button
          type="button"
          onClick={() => composeChat({ text: seedText })}
          className="rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Ask about this
        </button>
        {event.entity_slug && (
          <button
            type="button"
            onClick={() => goTo("subjects", { mode: "wiki", entity: event.entity_slug! })}
            className="rounded-md border border-border/60 px-2 py-1.5 text-label-xs text-muted-foreground hover:bg-accent/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            Open in Wiki →
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Track detail card with extended fields (deliverable #2)
// ---------------------------------------------------------------------------

interface TrackDetailCardProps {
  pinnedTrack: PinnedTrack
  trackLoading: boolean
  /** Base TrackEvent[] from the existing hook */
  legacyEvents: import("@/lib/api/graph").TrackEvent[]
  /** Extended fields from TimelineTrackExtension (null until A's backend lands) */
  extension: TimelineTrackExtension | null
  onClose: () => void
  onEntityPick?: (id: string) => void
}

function TrackDetailCard({
  pinnedTrack,
  trackLoading,
  legacyEvents,
  extension,
  onClose,
  onEntityPick,
}: TrackDetailCardProps) {
  const { goTo, composeChat } = useNavigation()

  return (
    <div
      className="absolute bottom-12 right-3 w-80 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur"
      data-testid="track-detail-card"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">
            {pinnedTrack.name}
          </div>
          <div className="mt-0.5 text-label-xs text-muted-foreground">
            <span className="uppercase">{pinnedTrack.trustState}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close entity card"
          className="rounded p-1 text-muted-foreground hover:bg-accent/40"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Verification aggregate (new field from A, amendment #2 sparse suppression) */}
      {extension?.verification && extension.verification.reports >= 3 && (
        <div className="mt-2 flex items-center gap-1.5 rounded-md border border-border/40 bg-background/60 px-2 py-1.5 text-label-xs">
          <VerificationStateIcon score={extension.verification.overall_score_avg} />
          <span className="text-muted-foreground">
            {extension.verification.reports} reports · {Math.round(extension.verification.overall_score_avg * 100)}% avg score
          </span>
        </div>
      )}

      {/* New entities (new field from A) */}
      {extension?.new_entities && extension.new_entities.length > 0 && (
        <div className="mt-2">
          <div className="text-label-xs font-medium text-muted-foreground">New entities</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {extension.new_entities.map((e) => (
              <button
                key={e.slug}
                type="button"
                onClick={() => goTo("subjects", { mode: "wiki", entity: e.slug })}
                className="rounded-full border border-border/60 bg-card/70 px-2 py-0.5 text-label-xs text-foreground hover:bg-accent/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              >
                {e.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Community summary (community lens only, new field from A) */}
      {extension?.community_summary && (
        <p className="mt-2 rounded-md bg-muted/40 px-2.5 py-1.5 text-label-xs text-muted-foreground leading-relaxed">
          {extension.community_summary}
        </p>
      )}

      {/* Track events — loading state */}
      {trackLoading && (
        <div className="mt-2 flex items-center gap-1.5 text-label-xs text-muted-foreground">
          <Clock className="h-3 w-3 animate-spin" />
          Loading events…
        </div>
      )}

      {/* Extended events from A (new shape) */}
      {!trackLoading && extension?.knowledge_events && extension.knowledge_events.length > 0 && (
        <ExtendedEventsList events={extension.knowledge_events} />
      )}

      {/* Legacy events fallback (existing shape until A lands) */}
      {!trackLoading && (!extension?.knowledge_events || extension.knowledge_events.length === 0) && legacyEvents.length > 0 && (
        <div className="mt-2 max-h-48 overflow-y-auto">
          <div className="mb-1 text-label-xs font-medium text-muted-foreground">Recent events</div>
          <ul className="flex flex-col gap-1.5">
            {legacyEvents.slice(0, 8).map((ev, i) => (
              <li key={i} className="rounded-md border border-border/40 bg-background/60 px-2 py-1.5 text-label-xs">
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate font-medium text-foreground">
                    {ev.artifact_filename}
                  </span>
                  <span className="shrink-0 text-muted-foreground tabular-nums">
                    {ev.ts.slice(0, 10)}
                  </span>
                </div>
                {ev.summary && (
                  <p className="mt-0.5 line-clamp-2 text-muted-foreground/80">{ev.summary}</p>
                )}
                {ev.co_mentioned.length > 0 && (
                  <div className="mt-0.5 truncate text-muted-foreground/60">
                    with {ev.co_mentioned.slice(0, 3).map((c) => c.name).join(", ")}
                    {ev.co_mentioned.length > 3 && ` +${ev.co_mentioned.length - 3}`}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Empty events state */}
      {!trackLoading && legacyEvents.length === 0 && (!extension?.knowledge_events || extension.knowledge_events.length === 0) && (
        <p className="mt-2 text-label-xs text-muted-foreground">No knowledge events in this window.</p>
      )}

      {/* Actions */}
      <div className="mt-2 flex gap-1.5">
        <button
          type="button"
          onClick={() => composeChat({ text: `Summarise knowledge activity for ${pinnedTrack.name}` })}
          className="flex-1 rounded-md border border-border/60 px-2 py-1.5 text-label-xs text-muted-foreground hover:bg-accent/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Ask about this
        </button>
        <button
          type="button"
          onClick={() => {
            if (onEntityPick) onEntityPick(pinnedTrack.canonicalId)
            goTo("subjects", { mode: "wiki", entity: pinnedTrack.canonicalId })
          }}
          className="flex-1 rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Open in Wiki
        </button>
      </div>
    </div>
  )
}

function ExtendedEventsList({ events }: { events: TrackEventExtended[] }) {
  const { composeChat } = useNavigation()

  return (
    <div className="mt-2 max-h-48 overflow-y-auto">
      <div className="mb-1 text-label-xs font-medium text-muted-foreground">Knowledge events</div>
      <ul className="flex flex-col gap-1.5">
        {events.slice(0, 8).map((ev, i) => (
          <li key={i}>
            <button
              type="button"
              className="w-full rounded-md border border-border/40 bg-background/60 px-2 py-1.5 text-left text-label-xs hover:bg-accent/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              onClick={() => composeChat({ text: ev.summary ?? `Tell me about the ${ev.kind} event for ${ev.entity_name}` })}
            >
              <div className="flex items-center justify-between gap-1">
                <span className="truncate font-medium text-foreground">
                  {ev.entity_name}
                </span>
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {ev.ts.slice(0, 10)}
                </span>
              </div>
              {ev.summary && (
                <p className="mt-0.5 line-clamp-2 text-muted-foreground/80">{ev.summary}</p>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface TimelineProps {
  onEntityPick?: (id: string) => void
  /** Focal-entity handoff ("Open in Timeline" from Atlas/Wiki) — auto-pins
   *  that entity's track so the view lands on its history rather than the
   *  unfiltered global stratigraph. */
  focalEntity?: string | null
}

export function Timeline({ onEntityPick, focalEntity }: TimelineProps) {
  const { goTo } = useNavigation()

  // Config — loaded from localStorage
  const [config, setConfig] = useState<TimelineConfig>(loadTimelineConfig)
  const handleConfig = useCallback((patch: Partial<TimelineConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch }
      saveTimelineConfig(next)
      return next
    })
  }, [])

  // Write lastViewedAt on unmount (not on mount, so the band survives the session)
  useEffect(() => {
    return () => {
      setConfig((prev) => {
        const next = stampLastViewed(prev)
        saveTimelineConfig(next)
        return next
      })
    }
  }, [])

  // Period tab is mirrored from config so that period tabs write back to config
  const period = config.period
  const handlePeriod = useCallback((p: TimelineConfig["period"]) => {
    handleConfig({ period: p })
  }, [handleConfig])

  // Lens + type filter — default to "domain" per amendment #1 gating note
  const [lens, setLens] = useState<TimelineLens>("cluster")
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set())
  const toggleType = useCallback((t: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  // Amendment #1: freeze/re-rank apparatus gated OFF when lens !== "community"/"cluster"
  // The apparatus stays in the code but is inert for domain/type/trust lenses.
  const freezeReRankActive = lens === "cluster"

  // Frozen stratum order (active only when freezeReRankActive)
  const [frozenOrder, setFrozenOrder] = useState<string[] | null>(null)
  const [reRankAvailable, setReRankAvailable] = useState(false)
  const windowRankingRef = useRef<string[] | null>(null)
  const handleReRank = useCallback(() => {
    if (!freezeReRankActive) return
    setFrozenOrder(windowRankingRef.current?.length ? windowRankingRef.current : null)
    setReRankAvailable(false)
  }, [freezeReRankActive])

  // Cards
  const [pinnedCommunity, setPinnedCommunity] = useState<PinnedCommunity | null>(null)
  const [pinnedTrack, setPinnedTrack] = useState<PinnedTrack | null>(null)

  // Bucket detail card state (L2 click — deliverable #1)
  const [bucketDetail, setBucketDetail] = useState<{
    laneId: string
    bucketDate: string
    mentionCount: number
    events: StrataEvent[]
    verification: import("./stratigraph/strata-types").VerificationAggregate | null
    topEntities: import("./stratigraph/strata-types").TopEntity[]
  } | null>(null)

  // Event detail card state (L2 event glyph click — deliverable #1)
  const [selectedEvent, setSelectedEvent] = useState<StrataEvent | null>(null)

  // Brush window — pure client viewport state, never feeds the strata query
  const [brushWindow, setBrushWindow] = useState<{ from: string; to: string } | null>(null)
  const handleBrushChange = useCallback((from: string, to: string) => {
    setBrushWindow({ from, to })
  }, [])

  // Brush re-center action (nearest-activity jump, amendment #3): centers a
  // 14-day window on the target date and drives the canvas brush silently.
  const [brushTarget, setBrushTarget] = useState<{ from: string; to: string; nonce: number } | null>(null)
  const brushRecenterRef = useRef<((date: string) => void) | null>(null)
  brushRecenterRef.current = (date: string) => {
    const center = new Date(date).getTime()
    const HALF_WINDOW_MS = 7 * 24 * 3600 * 1000
    const from = new Date(center - HALF_WINDOW_MS).toISOString().slice(0, 10)
    const to = new Date(center + HALF_WINDOW_MS).toISOString().slice(0, 10)
    setBrushWindow({ from, to })
    setBrushTarget((prev) => ({ from, to, nonce: (prev?.nonce ?? 0) + 1 }))
  }

  // Track detail — lazy-load events when a track is pinned
  const { data: trackDetail, isLoading: trackLoading } = useTimelineTrack({
    canonicalId: pinnedTrack?.canonicalId ?? null,
    from: brushWindow?.from,
    to: brushWindow?.to,
    enabled: pinnedTrack !== null,
  })

  // LOD state
  const [lodLevel, setLodLevel] = useState<"era" | "bucket" | "track">("era")
  const handleLODChange = useCallback((level: "era" | "bucket" | "track") => {
    setLodLevel(level)
  }, [])

  // Tokens
  const [tokens, setTokens] = useState<MapTokens>(() => {
    if (typeof document !== "undefined") return resolveMapTokens(document.documentElement)
    return {
      clusters: Array(8).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      clusterOther: "#999", // drift-allowed: SSR fallback only, never reaches browser
      domains: Array(12).fill("#999"), // drift-allowed: SSR fallback only, never reaches browser
      domainOther: "#666", // drift-allowed: SSR fallback only, never reaches browser
      edge: "#ccc", // drift-allowed: SSR fallback only, never reaches browser
      dim: "#eee", // drift-allowed: SSR fallback only, never reaches browser
      interaction: "#00c8b4", // drift-allowed: SSR fallback only, never reaches browser
      foreground: "#111", // drift-allowed: SSR fallback only, never reaches browser
      background: "#f5f5f5", // drift-allowed: SSR fallback only, never reaches browser
      trustVerified: "#333", // drift-allowed: SSR fallback only, never reaches browser
      trustPartial: "#555", // drift-allowed: SSR fallback only, never reaches browser
      trustUnverified: "#888", // drift-allowed: SSR fallback only, never reaches browser
      graphite: "#6b7080", // drift-allowed: SSR fallback only, never reaches browser
      grid: "#eee", // drift-allowed: SSR fallback only, never reaches browser
    }
  })

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTokens(resolveMapTokens(document.documentElement))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  // Reduced motion
  const reducedMotion = useMemo(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  )

  // Data fetch — keyed by period only; the brush zooms within this window.
  const { data, isLoading, isError, error } = useTimelineStrata({ period })

  // Freeze stratum order on first load (only when cluster lens is active)
  useEffect(() => {
    if (!data || frozenOrder !== null || !freezeReRankActive) return
    const ranked = data.communities.filter((c) => !c.is_other)
      .sort((a, b) => b.total_mentions - a.total_mentions)
      .map((c) => c.community_id)
    setFrozenOrder(ranked)
  }, [data, frozenOrder, freezeReRankActive])

  // Re-rank availability (amendment #1: only active for cluster lens)
  const windowRanking = useMemo(() => {
    if (!data || !freezeReRankActive) return null
    const lo = brushWindow?.from ?? null
    const hi = brushWindow?.to ?? null
    const sums = new Map<string, number>()
    for (const s of data.series) {
      if (s.community_id === "other") continue
      let total = 0
      for (let i = 0; i < s.buckets.length; i++) {
        const d = data.bucket_dates[i]
        if ((lo && d < lo) || (hi && d > hi)) continue
        total += s.buckets[i] ?? 0
      }
      if (total > 0) sums.set(s.community_id, (sums.get(s.community_id) ?? 0) + total)
    }
    return [...sums.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([id]) => id)
      .slice(0, 8)
  }, [data, brushWindow, freezeReRankActive])
  windowRankingRef.current = windowRanking

  useEffect(() => {
    if (!frozenOrder || !windowRanking || !freezeReRankActive) {
      setReRankAvailable(false)
      return
    }
    const frozenTop = frozenOrder.slice(0, 8)
    setReRankAvailable(
      windowRanking.some((id, i) => frozenTop[i] !== id),
    )
  }, [frozenOrder, windowRanking, freezeReRankActive])

  // Entity type chips from series
  const typeChips = useMemo(() => {
    if (!data) return []
    const counts = new Map<string, number>()
    for (const s of data.series) {
      const total = s.buckets.reduce((a, b) => a + b, 0)
      counts.set(s.entity_type, (counts.get(s.entity_type) ?? 0) + total)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
  }, [data])

  // Pinned entity ids (entity tracks, not generalized pinned items)
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set())
  const togglePin = useCallback((id: string) => {
    setPinnedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Pinned track entries for the shelf
  const pinnedTrackEntries = data?.tracks.filter((t) => pinnedIds.has(t.canonical_id)) ?? []

  // Focal-entity handoff: once strata data is in, auto-pin the focal track.
  // Applies once per focal id; the entity may be outside the track budget,
  // in which case the lazy track fetch fills in its real name below.
  const focalApplied = useRef<string | null>(null)
  useEffect(() => {
    if (!focalEntity || !data || focalApplied.current === focalEntity) return
    focalApplied.current = focalEntity
    const track = data.tracks.find((t) => t.canonical_id === focalEntity)
    setPinnedTrack({
      canonicalId: focalEntity,
      name: track?.name ?? focalEntity,
      communityId: track?.community_id ?? "",
      trustState: track?.trust_state ?? "unknown",
    })
    setPinnedIds((prev) => {
      const next = new Set(prev)
      next.add(focalEntity)
      return next
    })
    setPinnedCommunity(null)
    setBucketDetail(null)
    setSelectedEvent(null)
  }, [focalEntity, data])

  // Patch the placeholder name once the track detail lands (budget-cut case).
  useEffect(() => {
    if (
      trackDetail?.name &&
      pinnedTrack &&
      trackDetail.canonical_id === pinnedTrack.canonicalId &&
      pinnedTrack.name !== trackDetail.name
    ) {
      setPinnedTrack({ ...pinnedTrack, name: trackDetail.name })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackDetail])

  // Lane metadata lookup (from extended payload, if present)
  const lanesMetaMap = useMemo(() => {
    const ext = (data as (typeof data & { lanes?: LaneMeta[] }) | undefined)
    if (!ext?.lanes) return new Map<string, LaneMeta>()
    return new Map(ext.lanes.map((l) => [l.lane_id, l]))
  }, [data])

  // Since-you-last-looked marker (deliverable #3). Per-lane deltas are
  // computed client-side from the series buckets after lastViewedAt — the
  // mention data is already in the payload, no extra fetch.
  const sinceMarker = useMemo((): SinceMarker => {
    const deltaByLane: SinceMarker["deltaByLane"] = {}
    if (config.lastViewedAt && data) {
      const sinceKey = config.lastViewedAt.slice(0, 10)
      for (const s of data.series) {
        const laneId = lens === "domain"
          ? ((s as { domain?: string }).domain ?? "other")
          : s.community_id
        let mentions = 0
        for (let i = 0; i < data.bucket_dates.length; i++) {
          if (data.bucket_dates[i] >= sinceKey) mentions += s.buckets[i] ?? 0
        }
        if (mentions > 0) {
          const cur = deltaByLane[laneId] ?? { mentions: 0, refreshes: 0, contradictions: 0 }
          cur.mentions += mentions
          deltaByLane[laneId] = cur
        }
      }
      for (const ev of data.events ?? []) {
        if (ev.ts < config.lastViewedAt) continue
        const cur = deltaByLane[ev.lane_id] ?? { mentions: 0, refreshes: 0, contradictions: 0 }
        if (ev.kind === "contradict" || ev.kind === "contradiction_finding") cur.contradictions += 1
        else cur.refreshes += 1
        deltaByLane[ev.lane_id] = cur
      }
    }
    return { lastViewedAt: config.lastViewedAt, deltaByLane }
  }, [config.lastViewedAt, data, lens])

  // Nearest activity date (for empty-window message, amendment #3)
  // Reads from payload if available — falls back to null (bare message not shown)
  const nearestActivity = useMemo(() => {
    if (!data || data.bucket_dates.length === 0) return null
    // Find the bucket with the most mentions outside the current brush window
    let bestDate: string | null = null
    let bestCount = 0
    for (const s of data.series) {
      for (let i = 0; i < s.buckets.length; i++) {
        const d = data.bucket_dates[i]
        const count = s.buckets[i] ?? 0
        // Only consider dates outside the current brush window
        const inWindow = brushWindow
          ? d >= brushWindow.from && d <= brushWindow.to
          : true
        if (!inWindow && count > bestCount) {
          bestCount = count
          bestDate = d
        }
      }
    }
    return bestDate && bestCount > 0 ? { date: bestDate, count: bestCount } : null
  }, [data, brushWindow])

  // ---------------------------------------------------------------------------
  // 4-state matrix
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <div className="flex h-full w-full flex-col gap-3 p-4" role="status" aria-busy="true" aria-label="Loading timeline">
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="ml-auto h-5 w-32" />
        </div>
        {/* Lane-shaped skeletons per D.2 contract */}
        <div className="flex flex-col gap-2 flex-1">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    const errMsg = error instanceof Error ? error.message : "Failed to load timeline strata."
    // 412 renders as configuration guidance (amendment #1 from D.2)
    const is412 = errMsg.includes("412") || errMsg.toLowerCase().includes("configuration")
    return (
      <div className="flex h-full w-full items-center justify-center p-6" data-testid="timeline-error">
        <Alert variant="destructive" className="max-w-md" role="alert">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {is412
              ? "Timeline requires configuration before it can load. Check your knowledge graph connection in Settings."
              : errMsg}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  // bucket_dates are DATE-generated (31 entries even for an empty corpus),
  // so emptiness must key on the data totals — with bucket-only checking the
  // empty state was unreachable and an all-zero corpus rendered a dead
  // canvas (found 2026-07-10).
  if (!data || data.bucket_dates.length === 0 || data.totals.mentions === 0) {
    return (
      <div className="flex h-full w-full items-center justify-center p-12" data-testid="timeline-empty">
        <EmptyState
          icon={Layers}
          title="No knowledge activity yet"
          description="The stratigraph builds after your first ingestion. Ingest a document and activity will appear here."
        />
      </div>
    )
  }

  // Populated state — check whether the brushed window has zero activity
  const windowHasMentions = data.series.some((s) =>
    s.buckets.some((count, i) => {
      const d = data.bucket_dates[i]
      if (!brushWindow) return count > 0
      return count > 0 && d >= brushWindow.from && d <= brushWindow.to
    }),
  )

  // ---------------------------------------------------------------------------
  // Populated state
  // ---------------------------------------------------------------------------

  return (
    <div
      className="relative flex h-full w-full flex-col bg-background"
      data-testid="timeline-mode"
    >
      {/* Header toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border/40 px-3 py-1.5">
        {/* Lens radiogroup — parity with Constellation */}
        <div
          className="flex items-center gap-0.5 rounded-lg border border-border/60 bg-card/80 p-0.5 backdrop-blur"
          role="radiogroup"
          aria-label="Color lens"
        >
          {LENSES.map((l) => (
            <button
              key={l.id}
              type="button"
              role="radio"
              aria-checked={lens === l.id}
              onClick={() => setLens(l.id)}
              title={l.hint}
              className={`rounded-md px-2 py-1 text-label-xs transition-colors ${
                lens === l.id
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>

        {/* Type filter chips */}
        {typeChips.length > 1 && (
          <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by entity type">
            {typeChips.map(([t, n]) => (
              <button
                key={t}
                type="button"
                aria-pressed={typeFilter.has(t)}
                onClick={() => toggleType(t)}
                className={`rounded-full border px-2 py-0.5 text-label-xs transition-colors ${
                  typeFilter.has(t)
                    ? "border-accent bg-accent/30 text-accent-foreground"
                    : "border-border/60 bg-card/70 text-muted-foreground hover:bg-accent/20"
                }`}
              >
                {t} <span className="opacity-60">{n}</span>
              </button>
            ))}
            {typeFilter.size > 0 && (
              <button
                type="button"
                onClick={() => setTypeFilter(new Set())}
                className="rounded-full px-2 py-0.5 text-label-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
          </div>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {/* Re-rank badge — amendment #1: only shown when cluster lens is active */}
          {freezeReRankActive && reRankAvailable && (
            <button
              type="button"
              onClick={handleReRank}
              className="rounded-lg border border-border/60 bg-card/80 px-2 py-1 text-label-xs text-muted-foreground backdrop-blur hover:bg-accent/40"
              title="Community ranking has shifted; click to re-rank for this window"
            >
              Re-rank
            </button>
          )}

          {/* Period tabs */}
          <div role="tablist" aria-label="Time period" className="flex gap-0.5">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                role="tab"
                aria-selected={p.value === period}
                onClick={() => handlePeriod(p.value)}
                className={`rounded px-2 py-0.5 text-label-xs transition-colors ${
                  p.value === period
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/40"
                }`}
                data-testid={`timeline-period-${p.value}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Config popover */}
          <TimelineConfigPanel config={config} onChange={handleConfig} />
        </div>
      </div>

      {/* Pinned shelf — generalized to lane|event (amendment #7) */}
      {pinnedTrackEntries.length > 0 && (
        <div
          className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border/40 px-3 py-1"
          role="list"
          aria-label="Pinned entities"
        >
          {pinnedTrackEntries.map((t) => {
            // Amendment #7 hue fix: route through resolveMapTokens with active colorFamily.
            // Domain lens → domain palette; cluster/other → cluster palette.
            const isDomainLens = lens === "domain"
            let hueColor: string
            if (isDomainLens) {
              const domainKey = t.primary_domain ?? ""
              // domainSlot is a stable hash into 0..11 domain slots
              const slot = domainKey
                ? Math.abs(Array.from(domainKey).reduce((h, c) => ((h << 5) - h) + c.charCodeAt(0) | 0, 0)) % 12
                : 11
              hueColor = tokens.domains[slot] ?? tokens.domainOther
            } else {
              const slot = communitySlot(t.community_id)
              hueColor = tokens.clusters[slot] ?? tokens.clusterOther
            }
            return (
              <div
                key={t.canonical_id}
                role="listitem"
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-border/60 bg-card/80 px-2 py-1 text-label-xs"
                style={{ borderLeftColor: hueColor, borderLeftWidth: 3 }} // drift-allowed: runtime stratum hue tint
              >
                <span className="text-foreground">{t.name}</span>
                <button
                  type="button"
                  aria-label={`Unpin ${t.name}`}
                  onClick={() => togglePin(t.canonical_id)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {/* Pre-ledger InfoTip (D.2 degraded state) */}
      {(data as (typeof data & { ledger_start_date?: string | null }) | undefined)?.ledger_start_date && (
        <div className="flex shrink-0 items-center gap-1.5 border-b border-dashed border-border/30 px-3 py-1 text-label-xxs text-muted-foreground/60">
          <InfoTip term="event-ledger" />
          <span>
            Event ledger begins{" "}
            {formatBucketDate((data as typeof data & { ledger_start_date: string }).ledger_start_date)}
            {" "} — earlier strata show density only
          </span>
        </div>
      )}

      {/* Redis label artifact degraded state */}
      {lanesMetaMap.size === 0 && data.totals.entities_introduced > 0 && (
        <div className="flex shrink-0 items-center gap-1.5 border-b border-dashed border-amber-500/20 px-3 py-0.5 text-label-xxs text-muted-foreground/60">
          <InfoTip term="lane-labels-degraded" />
          <span>Lane labels degraded — weekly refresh pending</span>
        </div>
      )}

      {/* Canvas — fills remaining space */}
      <div className="relative min-h-0 flex-1">
        <StratigraphCanvas
          data={data}
          lens={lens}
          typeFilter={typeFilter}
          pinnedIds={pinnedIds}
          frozenOrder={freezeReRankActive ? frozenOrder : null}
          trackBudget={config.trackBudget}
          markersVisible={config.markersVisible}
          ingestHatch={config.ingestHatch}
          lodLevel={lodLevel}
          onLODChange={handleLODChange}
          tokens={tokens}
          sinceMarker={sinceMarker}
          brushTarget={brushTarget}
          onCommunityClick={(community) => {
            setPinnedCommunity((prev) =>
              prev?.communityId === community.communityId ? null : community
            )
            setPinnedTrack(null)
            setBucketDetail(null)
            setSelectedEvent(null)
          }}
          onTrackClick={(track) => {
            setPinnedTrack((prev) =>
              prev?.canonicalId === track.canonicalId ? null : track
            )
            togglePin(track.canonicalId)
            setPinnedCommunity(null)
            setBucketDetail(null)
            setSelectedEvent(null)
          }}
          onBrushChange={handleBrushChange}
          onEventClick={(event: StrataEvent) => {
            setSelectedEvent((prev) =>
              prev?.ts === event.ts && prev.kind === event.kind ? null : event
            )
            setBucketDetail(null)
            setPinnedCommunity(null)
          }}
          reducedMotion={reducedMotion}
        />

        {/* Empty window state — nearest-activity jump (amendment #3) */}
        {!windowHasMentions && brushWindow && (
          <div
            className="absolute inset-x-0 top-1/3 mx-auto flex max-w-sm flex-col items-center gap-2 text-center"
            data-testid="timeline-window-empty"
          >
            <p className="text-label-xs text-muted-foreground">
              No activity {brushWindow.from.slice(0, 10)} – {brushWindow.to.slice(0, 10)}.
            </p>
            {nearestActivity && (
              <button
                type="button"
                className="flex items-center gap-1 rounded-md border border-brand/40 bg-brand/5 px-3 py-1.5 text-label-xs text-muted-foreground hover:bg-brand/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => {
                  brushRecenterRef.current?.(nearestActivity.date)
                }}
              >
                Nearest activity: {formatBucketDate(nearestActivity.date)}{" "}
                ({nearestActivity.count.toLocaleString()} mentions)
                <ArrowRight className="h-3 w-3 ml-0.5" aria-hidden="true" />
              </button>
            )}
          </div>
        )}

        {/* Community card */}
        {pinnedCommunity && (
          <div className="absolute bottom-12 left-3 w-72 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {pinnedCommunity.label}
                </div>
                <div className="mt-0.5 text-label-xs text-muted-foreground">
                  {pinnedCommunity.totalMentions.toLocaleString()} mentions
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPinnedCommunity(null)}
                aria-label="Close community card"
                className="rounded p-1 text-muted-foreground hover:bg-accent/40"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            {pinnedCommunity.topHubs.length > 0 && (
              <div className="mt-2">
                <div className="text-label-xs font-medium text-muted-foreground">Top entities</div>
                <div className="mt-1 flex flex-col gap-0.5">
                  {pinnedCommunity.topHubs.slice(0, 5).map((hub) => (
                    <button
                      key={hub.canonical_id}
                      type="button"
                      onClick={() => onEntityPick?.(hub.canonical_id)}
                      className="rounded px-1.5 py-0.5 text-left text-label-xs text-foreground hover:bg-accent/30"
                    >
                      {hub.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <button
              type="button"
              onClick={() => goTo("subjects", { mode: "atlas" })}
              className="mt-2 w-full rounded-md bg-accent px-2 py-1.5 text-label-xs font-medium text-accent-foreground hover:bg-accent/80"
            >
              Open in Atlas
            </button>
          </div>
        )}

        {/* Event detail card (L2 event glyph click) */}
        {selectedEvent && !pinnedTrack && (
          <div className="absolute bottom-12 right-3 w-80 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <EventDetailCard
              event={selectedEvent}
              onClose={() => setSelectedEvent(null)}
            />
          </div>
        )}

        {/* Bucket detail card (L2 bucket click — deliverable #1) */}
        {bucketDetail && !pinnedTrack && !selectedEvent && (
          <div className="absolute bottom-12 right-3 w-80 rounded-lg border border-border/60 bg-card/95 p-3 shadow-xl backdrop-blur">
            <BucketDetailCard
              bucketDate={bucketDetail.bucketDate}
              laneId={bucketDetail.laneId}
              laneMeta={lanesMetaMap.get(bucketDetail.laneId)}
              mentionCount={bucketDetail.mentionCount}
              events={bucketDetail.events}
              verification={bucketDetail.verification}
              topEntities={bucketDetail.topEntities}
              communityLensActive={lens === "cluster"}
              onClose={() => setBucketDetail(null)}
              onEventClick={(ev) => {
                setSelectedEvent(ev)
                setBucketDetail(null)
              }}
            />
          </div>
        )}

        {/* Track detail card (deliverable #2) */}
        {pinnedTrack && (
          <TrackDetailCard
            pinnedTrack={pinnedTrack}
            trackLoading={trackLoading}
            legacyEvents={trackDetail?.events ?? []}
            extension={
              // Only treat as extended when Agent A's new_entities field is present
              trackDetail && "new_entities" in trackDetail
                ? (trackDetail as typeof trackDetail & TimelineTrackExtension)
                : null
            }
            onClose={() => {
              setPinnedTrack(null)
              togglePin(pinnedTrack.canonicalId)
            }}
            onEntityPick={onEntityPick}
          />
        )}
      </div>
    </div>
  )
}

export default Timeline

// Re-export PinnedItem types so tests can construct fixtures without importing config directly
export type { PinnedItem, PinnedEventItem }
