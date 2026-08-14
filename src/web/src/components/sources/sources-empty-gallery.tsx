// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F1 — Sources empty-state gallery.
 *
 * Renders the 22 supported source kinds as a clickable tile grid,
 * Core tiles unlocked, Pro tiles wearing the lock badge. Click-through
 * opens the F3 wizard pre-filled with the selected kind.
 *
 * Brand surfaces: NOT Liquid Glass (that budget is reserved for the
 * 9 hero surfaces). Plain border + .cerid-press + .cerid-stagger for
 * the entrance cascade.
 */

import { useEffect } from "react"
import { Cable, Clock, Lock, Monitor } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { DataState } from "@/components/ui/data-state"
import { listSourceKinds } from "@/lib/api/sources"
import { useEntitlements } from "@/hooks/use-entitlements"
import { EntitlementsUnavailableNote } from "@/components/shared/entitlements-error-notice"
import type { SourceKindMetaExt } from "./source-kind-meta"
import { descriptorFor } from "./source-kind-icons"

interface SourcesEmptyGalleryProps {
  onSelectKind: (kind: string) => void
  /** Opens the ConnectorDetail flow for an OAuth/system-permission kind.
      When omitted, oauth tiles stay disabled with copy naming the
      Sources → Connectors tab (where the connector rows live). */
  onOpenConnector?: (kind: string) => void
  /** Called instead of the normal open when a Pro tile is clicked on a tier
      that can't use it. Without this the wizard opens and then fails at the
      server gate — the tile advertises the feature and dead-ends on it. */
  onProLocked?: (kind: string) => void
}

export function SourcesEmptyGallery({
  onSelectKind,
  onOpenConnector,
  onProLocked,
}: SourcesEmptyGalleryProps) {
  // Per-FLAG resolution: /sources/kinds carries the FEATURE_FLAGS key gating
  // each kind, so the verdict comes from the server's own flag detail rather
  // than a guessed tier mapping. The "pro" fallback keeps Pro kinds failing
  // CLOSED while capabilities are unknown (flag missing, fetch failed) —
  // same decision the add-source wizard pins in its Pro-gate tests. The
  // loading gate below keeps that fallback from painting a paying customer
  // an upgrade pitch on first paint; the server 403 stays authoritative.
  const { forFlag, isLoading: entLoading, isError: entitlementsError } = useEntitlements()
  const kindLocked = (k: SourceKindMetaExt) =>
    k.tier === "pro" &&
    forFlag(k.feature_flag ?? undefined, "pro").state === "locked"
  const { data: kinds, isLoading, isError, refetch } = useQuery<SourceKindMetaExt[]>({
    queryKey: ["source-kinds"],
    queryFn: listSourceKinds,
    staleTime: 60_000,
  })

  // Force re-trigger the cerid-stagger animation each mount.
  useEffect(() => {
    // no-op: keyed render covers it
  }, [])

  // `isLoading || !kinds` collapsed loading and failure into one branch, so a
  // failed fetch left "Loading sources…" on screen forever — the exact stuck-
  // spinner case DataState exists to prevent. This is a new user's first
  // screen; an unrecoverable spinner reads as a broken product.
  // `entLoading` is folded in so a paying customer never sees the Pro-locked
  // interception while capabilities are still in flight (tier defaults to
  // "community" until they load). A FAILED capabilities fetch does not block
  // the gallery — entLoading goes false and the tiles render with the
  // fail-closed fallback above.
  if (isLoading || entLoading || isError || !kinds) {
    return (
      <DataState
        loading={isLoading || entLoading}
        error={isError || (!kinds && !isLoading && !entLoading)}
        onRetry={() => void refetch()}
        loadingLabel="Loading sources…"
      />
    )
  }

  const core = kinds.filter((k) => k.tier === "core")
  const pro = kinds.filter((k) => k.tier === "pro")

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-6">
        <h2 className="text-lg font-medium text-foreground">Connect your first source</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          22 source kinds — 11 Core, 11 Pro. Click any tile to begin.
        </p>
      </header>

      <SectionTitle label="Core" subtitle={`${core.length} included with Cerid`} />
      <div className="cerid-stagger grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {core.map((k) => (
          <Tile key={k.kind} meta={k} onClick={() => onSelectKind(k.kind)} onOpenConnector={onOpenConnector} />
        ))}
      </div>

      {/* On a failed capabilities fetch every Pro kind wears the fail-closed
          registry fallback, so the section-level note is accurate whenever the
          fetch errored — the locks shown are unverified, not a plan verdict. */}
      <SectionTitle
        label="Pro"
        subtitle={
          entitlementsError
            ? "plan status unavailable"
            : `${pro.length} unlock with upgrade`
        }
        className="mt-8"
      />
      {entitlementsError && <EntitlementsUnavailableNote className="mb-2" />}
      <div className="cerid-stagger grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {pro.map((k) => {
          // "locked" is the only state an upgrade fixes; an entitled tier
          // whose flag is off (or on) takes the normal route and the server
          // answers. Resolved per kind so one disabled flag never locks the
          // whole Pro section.
          const locked = kindLocked(k)
          return (
            <Tile
              key={k.kind}
              meta={k}
              onClick={() =>
                locked && onProLocked ? onProLocked(k.kind) : onSelectKind(k.kind)
              }
              // A locked tile must reach the upgrade path even for kinds whose
              // normal route is the connector flow, so suppress that handoff.
              onOpenConnector={locked && onProLocked ? undefined : onOpenConnector}
              forceSelectable={locked && !!onProLocked}
            />
          )
        })}
      </div>
    </div>
  )
}

function SectionTitle({
  label,
  subtitle,
  className,
}: {
  label: string
  subtitle: string
  className?: string
}) {
  return (
    <div className={cn("mb-3 flex items-baseline justify-between", className)}>
      <h3 className="text-sm font-medium text-foreground">{label}</h3>
      <span className="text-label-xs text-muted-foreground">{subtitle}</span>
    </div>
  )
}

function Tile({
  meta,
  onClick,
  onOpenConnector,
  forceSelectable = false,
}: {
  meta: SourceKindMetaExt
  onClick: () => void
  onOpenConnector?: (kind: string) => void
  /** Keep the tile clickable even when its normal route is unavailable —
      used for Pro-locked tiles, whose click opens the upgrade dialog. A
      greyed-out tile can't sell anything. */
  forceSelectable?: boolean
}) {
  const desc = descriptorFor(meta.kind)
  const Icon = desc.icon
  const isPro = meta.tier === "pro"
  const availability = meta.availability ?? "available"
  // oauth kinds connect via the ConnectorDetail flow on this same
  // Sources → Connectors tab — actionable when the host wires it.
  const oauthActionable = availability === "oauth" && !!onOpenConnector
  // "coming soon" and "requires desktop" stay disabled regardless: those are
  // statements about the build, not about the plan, and no upgrade fixes them.
  const selectable =
    availability === "available" ||
    oauthActionable ||
    (forceSelectable && availability === "oauth")

  return (
    <button
      type="button"
      onClick={oauthActionable ? () => onOpenConnector(meta.kind) : onClick}
      disabled={!selectable}
      className={cn(
        "group relative flex flex-col items-start gap-2 rounded-lg border border-border/60 bg-card/40 px-4 py-3 text-left transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selectable
          ? "cerid-press hover:border-border hover:bg-card/70"
          : "cursor-not-allowed opacity-55",
      )}
      aria-label={
        availability === "oauth"
          ? oauthActionable
            ? `Connect ${desc.label}${isPro ? " (Pro)" : ""}`
            : `${desc.label} — set up in Sources → Connectors`
          : selectable
            ? `Add ${desc.label}${isPro ? " (Pro)" : ""}`
            : availability === "requires_desktop"
              ? `${desc.label} — requires the Cerid desktop app`
              : `${desc.label} — coming soon`
      }
    >
      <div className="flex w-full items-center justify-between">
        <Icon className="h-5 w-5 text-foreground/70 transition-colors group-hover:text-foreground" />
        {availability === "coming_soon" ? (
          <span
            className="inline-flex items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-label-xs font-medium text-muted-foreground"
            aria-label="Coming soon"
          >
            <Clock className="h-2.5 w-2.5" aria-hidden="true" />
            Soon
          </span>
        ) : availability === "requires_desktop" ? (
          <span
            className="inline-flex items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-label-xs font-medium text-muted-foreground"
            aria-label="Requires the Cerid desktop app"
          >
            <Monitor className="h-2.5 w-2.5" aria-hidden="true" />
            Desktop app
          </span>
        ) : availability === "oauth" ? (
          <span
            className="inline-flex items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-label-xs font-medium text-muted-foreground"
            aria-label="Connects from the Sources → Connectors tab"
          >
            <Cable className="h-2.5 w-2.5" aria-hidden="true" />
            Connector
          </span>
        ) : isPro ? (
          <span
            className="inline-flex items-center gap-0.5 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-label-xs font-medium text-amber-500"
            aria-label="Pro tier"
          >
            <Lock className="h-2.5 w-2.5" aria-hidden="true" />
            Pro
          </span>
        ) : null}
      </div>
      <div>
        <div className="text-sm font-medium text-foreground">{desc.label}</div>
        <div className="text-label-xs text-muted-foreground">
          {availability === "oauth"
            ? oauthActionable
              ? "OAuth or system permission — click to open the connector setup."
              : "OAuth or system permission — set up from the Sources → Connectors tab."
            : desc.blurb}
        </div>
      </div>
    </button>
  )
}
