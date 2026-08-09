// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// DOM icicle decomposition — the new Atlas default view.
// Tiered disclosure: T0 domains → T1 SubCategory (conditional) →
// T2 Community L1 → T3 Community L0 → T4 entity list (virtualized).
//
// Keyboard: Esc = collapse one tier, Shift+Esc = collapse to T0,
// Enter/Space = expand focused row; shadcn focus rings throughout.
// ARIA: tier context in announcements per A5.

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react"
import {
  ChevronRight,
  ChevronDown,
  Network,
  Layers,
  FolderOpen,
  Clock,
} from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/ui/empty-state"
import { LastUpdated } from "@/components/ui/last-updated"
import {
  NUMERIC_GARBAGE_RE,
  buildFallbackLabel,
  type DecompositionPayload,
  type DomainNode,
  type L1Community,
  type L0Community,
  type L0RollupBucket,
  type EntityLeaf,
  type AtlasTierPosition,
} from "@/lib/graph/cycle4-contracts"
import { domainColor } from "@/lib/graph/identity"
import { resolveMapTokens } from "@/components/subjects/constellation/map/community-layer"
import { useDecomposition, useCommunityEntities } from "./use-decomposition"
import type { OnInspect, OnFocusEntity } from "@/lib/graph/cycle4-contracts"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function effectiveLabel(
  label: string | undefined,
  size: number,
  topHubs: { id: string; name: string; degree: number }[],
): string {
  if (!label || NUMERIC_GARBAGE_RE.test(label)) {
    return buildFallbackLabel(size, topHubs)
  }
  return label
}

function getTokens() {
  if (typeof document === "undefined") return null
  return resolveMapTokens(document.documentElement)
}

// ---------------------------------------------------------------------------
// Tier state
// ---------------------------------------------------------------------------

type TierPath = string[] // [domainId, sub?, l1Id, l0Id]

interface IcicleState {
  // The currently expanded path (empty = T0 only visible)
  expandedPath: TierPath
  // Which entity list is open (l0 community id)
  openEntityList: string | null
  // Per-tier filter text
  domainFilter: string
  l1Filter: string
  l0Filter: string
  entityFilter: string
}

type IcicleAction =
  | { type: "EXPAND_DOMAIN"; domainId: string }
  | { type: "EXPAND_L1"; domainId: string; l1Id: string }
  | { type: "EXPAND_L0"; domainId: string; l1Id: string; l0Id: string }
  | { type: "OPEN_ENTITIES"; l0Id: string }
  | { type: "COLLAPSE_ONE" }
  | { type: "COLLAPSE_ALL" }
  | { type: "SET_PATH"; path: TierPath }
  | { type: "SET_DOMAIN_FILTER"; value: string }
  | { type: "SET_L1_FILTER"; value: string }
  | { type: "SET_L0_FILTER"; value: string }
  | { type: "SET_ENTITY_FILTER"; value: string }

function icicleReducer(state: IcicleState, action: IcicleAction): IcicleState {
  switch (action.type) {
    case "EXPAND_DOMAIN":
      return { ...state, expandedPath: [action.domainId], openEntityList: null, l1Filter: "", l0Filter: "", entityFilter: "" }
    case "EXPAND_L1":
      return { ...state, expandedPath: [action.domainId, action.l1Id], openEntityList: null, l0Filter: "", entityFilter: "" }
    case "EXPAND_L0":
      return { ...state, expandedPath: [action.domainId, action.l1Id, action.l0Id], openEntityList: null, entityFilter: "" }
    case "OPEN_ENTITIES":
      return { ...state, openEntityList: action.l0Id }
    case "COLLAPSE_ONE": {
      const p = state.expandedPath
      if (state.openEntityList) return { ...state, openEntityList: null }
      if (p.length === 0) return state
      return { ...state, expandedPath: p.slice(0, p.length - 1), openEntityList: null }
    }
    case "COLLAPSE_ALL":
      return { ...state, expandedPath: [], openEntityList: null, l1Filter: "", l0Filter: "", entityFilter: "" }
    case "SET_PATH":
      return { ...state, expandedPath: action.path, openEntityList: null }
    case "SET_DOMAIN_FILTER":
      return { ...state, domainFilter: action.value }
    case "SET_L1_FILTER":
      return { ...state, l1Filter: action.value }
    case "SET_L0_FILTER":
      return { ...state, l0Filter: action.value }
    case "SET_ENTITY_FILTER":
      return { ...state, entityFilter: action.value }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// ARIA live region — announces tier expansions
// ---------------------------------------------------------------------------

function useAnnounce() {
  const [msg, setMsg] = useState("")
  const announce = useCallback((text: string) => setMsg(text), [])
  return { msg, announce }
}

// ---------------------------------------------------------------------------
// Virtualized entity list (simple windowing — no new deps)
// ---------------------------------------------------------------------------

const ITEM_HEIGHT = 32
const VISIBLE_COUNT = 20

interface VirtualEntityListProps {
  entities: EntityLeaf[]
  filter: string
  pulseEntityId?: string | null
  domainId: string
  onInspect?: OnInspect
  onOpenNeighborhood?: (entityId: string) => void
}

function VirtualEntityList({
  entities,
  filter,
  pulseEntityId,
  onInspect,
  onOpenNeighborhood,
}: VirtualEntityListProps) {
  const [scrollTop, setScrollTop] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    if (!filter) return entities
    const q = filter.toLowerCase()
    return entities.filter((e) => e.name.toLowerCase().includes(q))
  }, [entities, filter])

  const totalHeight = filtered.length * ITEM_HEIGHT
  const startIdx = Math.floor(scrollTop / ITEM_HEIGHT)
  const endIdx = Math.min(startIdx + VISIBLE_COUNT + 2, filtered.length)
  const visibleItems = filtered.slice(startIdx, endIdx)
  const offsetTop = startIdx * ITEM_HEIGHT

  return (
    <div
      ref={containerRef}
      className="overflow-y-auto"
      style={{ height: Math.min(filtered.length * ITEM_HEIGHT, VISIBLE_COUNT * ITEM_HEIGHT) }} // drift-allowed: dynamic scroll area height
      onScroll={(e) => setScrollTop((e.currentTarget as HTMLDivElement).scrollTop)}
      role="list"
      aria-label="Entities in this cluster"
    >
      <div style={{ height: totalHeight, position: "relative" }}> {/* drift-allowed: virtual scroll layout */}
        <div style={{ transform: `translateY(${offsetTop}px)` }}> {/* drift-allowed: virtual scroll offset */}
          {visibleItems.map((entity) => {
            const isPulse = entity.id === pulseEntityId
            return (
              <div
                key={entity.id}
                role="listitem"
                className={`flex h-8 items-center justify-between gap-2 px-3 text-sm transition-colors hover:bg-accent/30 focus-within:bg-accent/20 ${
                  isPulse ? "animate-pulse bg-brand/10" : ""
                }`}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left text-foreground/90 focus-visible:outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px] rounded"
                  onClick={() => onInspect?.(entity.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onInspect?.(entity.id)
                    }
                  }}
                >
                  <span className="block truncate">{entity.name}</span>
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  <span className="rounded bg-accent/50 px-1 text-label-xs text-muted-foreground">
                    {entity.type}
                  </span>
                  <button
                    type="button"
                    aria-label={`Open ${entity.name} neighborhood`}
                    title="Open neighborhood"
                    className="rounded p-0.5 text-muted-foreground hover:bg-accent/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                    onClick={() => onOpenNeighborhood?.(entity.id)}
                  >
                    <Network className="h-3 w-3" aria-hidden="true" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tier row (generic expandable row)
// ---------------------------------------------------------------------------

interface TierRowProps {
  label: string
  count?: number
  expanded: boolean
  depth: number
  colorDot?: string
  dimmed?: boolean
  pulse?: boolean
  ariaLabel?: string
  onClick: () => void
  onKeyDown?: React.KeyboardEventHandler
  tabIndex?: number
  children?: React.ReactNode
  rightSlot?: React.ReactNode
  testId?: string
}

function TierRow({
  label,
  count,
  expanded,
  depth,
  colorDot,
  dimmed,
  pulse,
  ariaLabel,
  onClick,
  onKeyDown,
  tabIndex = 0,
  children,
  rightSlot,
  testId,
}: TierRowProps) {
  const indent = depth * 16
  const Icon = expanded ? ChevronDown : ChevronRight
  return (
    <div className={`group ${dimmed ? "opacity-40" : ""}`}>
      <div
        role="button"
        tabIndex={tabIndex}
        aria-label={ariaLabel ?? label}
        aria-expanded={expanded}
        onClick={onClick}
        onKeyDown={onKeyDown ?? ((e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick() }
        })}
        data-testid={testId}
        className={`flex h-8 items-center gap-2 rounded px-2 text-sm transition-colors hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px] ${
          expanded ? "bg-accent/20" : ""
        } ${pulse ? "animate-pulse bg-brand/10" : ""}`}
        style={{ paddingLeft: `${8 + indent}px` }} // drift-allowed: dynamic indent based on tier depth
      >
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {colorDot && (
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: colorDot }} // drift-allowed: domain-lens color from token registry, no raw hex
            aria-hidden="true"
          />
        )}
        <span className="flex-1 truncate font-medium text-foreground/90">{label}</span>
        {count !== undefined && (
          <span className="shrink-0 text-label-xs text-muted-foreground tabular-nums">{count}</span>
        )}
        {rightSlot}
      </div>
      {expanded && children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Filter input row
// ---------------------------------------------------------------------------

interface FilterInputProps {
  value: string
  onChange: (v: string) => void
  placeholder: string
  depth: number
}

function FilterInput({ value, onChange, placeholder, depth }: FilterInputProps) {
  return (
    <div
      className="flex items-center px-2 py-1"
      style={{ paddingLeft: `${8 + depth * 16 + 20}px` }} // drift-allowed: dynamic indent matching tier depth
    >
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full rounded border border-border/40 bg-background/60 px-2 py-0.5 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px]"
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// L0 tier row (with entity list expansion)
// ---------------------------------------------------------------------------

interface L0RowProps {
  l0: L0Community
  domainId: string
  l1Id: string
  expanded: boolean
  pulseEntityId?: string | null
  entityFilter: string
  onEntityFilterChange: (v: string) => void
  tokens: ReturnType<typeof resolveMapTokens>
  onExpand: () => void
  onInspect?: OnInspect
  onOpenNeighborhood?: (entityId: string) => void
  dimmed: boolean
  pulse: boolean
}

function L0Row({
  l0,
  expanded,
  pulseEntityId,
  entityFilter,
  onEntityFilterChange,
  tokens,
  onExpand,
  onInspect,
  onOpenNeighborhood,
  dimmed,
  pulse,
}: L0RowProps) {
  const label = effectiveLabel(l0.label, l0.size, l0.top_hubs)
  const isMixed = l0.purity < 0.7
  const color = isMixed
    ? tokens.domainOther
    : domainColor(tokens, l0.mode_domain)

  const { data: communityData, isLoading: entLoading } = useCommunityEntities(
    expanded ? l0.id : null,
  )

  return (
    <TierRow
      label={label}
      count={l0.size}
      expanded={expanded}
      depth={3}
      colorDot={color}
      dimmed={dimmed}
      pulse={pulse}
      ariaLabel={`${label}, cluster, ${l0.size} entities`}
      onClick={onExpand}
      testId={`l0-row-${l0.id}`}
      rightSlot={
        isMixed ? (
          <span className="text-label-xs text-muted-foreground/60">mixed</span>
        ) : null
      }
    >
      {expanded && (
        <div className="pb-1">
          <FilterInput
            value={entityFilter}
            onChange={onEntityFilterChange}
            placeholder="Filter entities…"
            depth={4}
          />
          {entLoading ? (
            <div className="px-4 py-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="mt-1 h-4 w-3/4" />
            </div>
          ) : communityData ? (
            <VirtualEntityList
              entities={communityData.entities}
              filter={entityFilter}
              pulseEntityId={pulseEntityId}
              domainId={l0.mode_domain}
              onInspect={onInspect}
              onOpenNeighborhood={onOpenNeighborhood}
            />
          ) : null}
        </div>
      )}
    </TierRow>
  )
}

// ---------------------------------------------------------------------------
// L1 tier (with L0 children)
// ---------------------------------------------------------------------------

interface L1SectionProps {
  l1: L1Community
  domainId: string
  expanded: boolean
  expandedL0Id: string | null
  pulseEntityId?: string | null
  l0Filter: string
  onL0FilterChange: (v: string) => void
  entityFilter: string
  onEntityFilterChange: (v: string) => void
  tokens: ReturnType<typeof resolveMapTokens>
  onExpand: () => void
  onExpandL0: (l0Id: string) => void
  onInspect?: OnInspect
  onOpenNeighborhood?: (entityId: string) => void
  dimmed: boolean
  pulse: boolean
}

function L1Section({
  l1,
  domainId,
  expanded,
  expandedL0Id,
  pulseEntityId,
  l0Filter,
  onL0FilterChange,
  entityFilter,
  onEntityFilterChange,
  tokens,
  onExpand,
  onExpandL0,
  onInspect,
  onOpenNeighborhood,
  dimmed,
  pulse,
}: L1SectionProps) {
  const label = effectiveLabel(l1.label, l1.size, l1.top_hubs)
  const color = domainColor(tokens, l1.mode_domain)

  const regularL0s = l1.children.filter(
    (c): c is L0Community => !("kind" in c),
  )
  const rollup = l1.children.find(
    (c): c is L0RollupBucket => "kind" in c && c.kind === "rollup",
  )

  const filteredL0s = l0Filter
    ? regularL0s.filter((l0) => {
        const lbl = effectiveLabel(l0.label, l0.size, l0.top_hubs)
        return lbl.toLowerCase().includes(l0Filter.toLowerCase())
      })
    : regularL0s

  return (
    <TierRow
      label={label}
      count={l1.size}
      expanded={expanded}
      depth={2}
      colorDot={color}
      dimmed={dimmed}
      pulse={pulse}
      ariaLabel={`${label}, community group, ${l1.size} entities`}
      onClick={onExpand}
      testId={`l1-row-${l1.id}`}
    >
      {expanded && (
        <div>
          <FilterInput
            value={l0Filter}
            onChange={onL0FilterChange}
            placeholder="Filter clusters…"
            depth={3}
          />
          {filteredL0s.map((l0) => (
            <L0Row
              key={l0.id}
              l0={l0}
              domainId={domainId}
              l1Id={l1.id}
              expanded={expandedL0Id === l0.id}
              pulseEntityId={pulseEntityId}
              entityFilter={expandedL0Id === l0.id ? entityFilter : ""}
              onEntityFilterChange={onEntityFilterChange}
              tokens={tokens}
              onExpand={() => onExpandL0(l0.id)}
              onInspect={onInspect}
              onOpenNeighborhood={onOpenNeighborhood}
              dimmed={!!(l0Filter && !effectiveLabel(l0.label, l0.size, l0.top_hubs).toLowerCase().includes(l0Filter.toLowerCase()))}
              pulse={false}
            />
          ))}
          {rollup && (
            <div
              className="flex h-8 items-center gap-2 px-2 text-sm text-muted-foreground/70"
              style={{ paddingLeft: `${8 + 3 * 16}px` }} // drift-allowed: dynamic indent for rollup row at depth 3
            >
              <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-50" aria-hidden="true" />
              <span className="flex-1 truncate italic">
                Smaller clusters ({rollup.community_count} · {rollup.entity_count} entities)
              </span>
            </div>
          )}
        </div>
      )}
    </TierRow>
  )
}

// ---------------------------------------------------------------------------
// Domain row
// ---------------------------------------------------------------------------

interface DomainSectionProps {
  domain: DomainNode
  expanded: boolean
  expandedL1Id: string | null
  expandedL0Id: string | null
  pulseEntityId?: string | null
  domainFilter: string
  l1Filter: string
  onL1FilterChange: (v: string) => void
  l0Filter: string
  onL0FilterChange: (v: string) => void
  entityFilter: string
  onEntityFilterChange: (v: string) => void
  tokens: ReturnType<typeof resolveMapTokens>
  onExpand: () => void
  onExpandL1: (l1Id: string) => void
  onExpandL0: (l1Id: string, l0Id: string) => void
  onInspect?: OnInspect
  onOpenNeighborhood?: (entityId: string) => void
  noCommunities?: boolean
  pulseDomain?: boolean
}

function DomainSection({
  domain,
  expanded,
  expandedL1Id,
  expandedL0Id,
  pulseEntityId,
  domainFilter,
  l1Filter,
  onL1FilterChange,
  l0Filter,
  onL0FilterChange,
  entityFilter,
  onEntityFilterChange,
  tokens,
  onExpand,
  onExpandL1,
  onExpandL0,
  onInspect,
  onOpenNeighborhood,
  noCommunities,
  pulseDomain,
}: DomainSectionProps) {
  const color = domainColor(tokens, domain.id)
  const isDimmed = domainFilter
    ? !domain.label.toLowerCase().includes(domainFilter.toLowerCase())
    : false

  const l1List = domain.communities ?? domain.subcategories?.flatMap((s) => s.children) ?? []

  const filteredL1s = l1Filter
    ? l1List.filter((l1) => {
        const lbl = effectiveLabel(l1.label, l1.size, l1.top_hubs)
        return lbl.toLowerCase().includes(l1Filter.toLowerCase())
      })
    : l1List

  return (
    <TierRow
      label={domain.label}
      count={domain.entity_count}
      expanded={expanded}
      depth={0}
      colorDot={color}
      dimmed={isDimmed}
      pulse={pulseDomain}
      ariaLabel={`${domain.label} domain, ${domain.entity_count} entities`}
      onClick={onExpand}
      testId={`domain-row-${domain.id}`}
    >
      {expanded && (
        <div>
          {noCommunities ? (
            <div className="px-4 py-2 text-sm text-muted-foreground italic">
              Clusters appear after the nightly analysis runs
            </div>
          ) : (
            <>
              <FilterInput
                value={l1Filter}
                onChange={onL1FilterChange}
                placeholder="Filter community groups…"
                depth={1}
              />
              {filteredL1s.map((l1) => (
                <L1Section
                  key={l1.id}
                  l1={l1}
                  domainId={domain.id}
                  expanded={expandedL1Id === l1.id}
                  expandedL0Id={expandedL1Id === l1.id ? expandedL0Id : null}
                  pulseEntityId={pulseEntityId}
                  l0Filter={expandedL1Id === l1.id ? l0Filter : ""}
                  onL0FilterChange={onL0FilterChange}
                  entityFilter={entityFilter}
                  onEntityFilterChange={onEntityFilterChange}
                  tokens={tokens}
                  onExpand={() => onExpandL1(l1.id)}
                  onExpandL0={(l0Id) => onExpandL0(l1.id, l0Id)}
                  onInspect={onInspect}
                  onOpenNeighborhood={onOpenNeighborhood}
                  dimmed={!!(l1Filter && !effectiveLabel(l1.label, l1.size, l1.top_hubs).toLowerCase().includes(l1Filter.toLowerCase()))}
                  pulse={false}
                />
              ))}
              {domain.unclustered.count > 0 && (
                <div
                  className="flex h-8 items-center gap-2 px-2 text-sm text-muted-foreground/70"
                  style={{ paddingLeft: `${8 + 1 * 16}px` }} // drift-allowed: dynamic indent for unclustered bucket at depth 1
                >
                  <span className="flex-1 truncate italic">
                    Unclustered ({domain.unclustered.count})
                    {domain.id === "digests" && " — no communities yet"}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </TierRow>
  )
}

// ---------------------------------------------------------------------------
// Breadcrumb
// ---------------------------------------------------------------------------

interface BreadcrumbProps {
  expandedPath: TierPath
  data: DecompositionPayload
  computedAt: string | null
  onCrumbClick: (depth: number) => void
}

function Breadcrumb({ expandedPath, data, computedAt, onCrumbClick }: BreadcrumbProps) {
  const crumbs: { label: string; depth: number }[] = [
    { label: "Overview", depth: -1 },
  ]

  if (expandedPath.length > 0) {
    const domainId = expandedPath[0]
    const domain = data.domains.find((d) => d.id === domainId)
    if (domain) crumbs.push({ label: domain.label, depth: 0 })
  }
  if (expandedPath.length > 1) {
    const domainId = expandedPath[0]
    const l1Id = expandedPath[1]
    const domain = data.domains.find((d) => d.id === domainId)
    const l1List = domain?.communities ?? domain?.subcategories?.flatMap((s) => s.children) ?? []
    const l1 = l1List.find((c) => c.id === l1Id)
    if (l1) crumbs.push({ label: effectiveLabel(l1.label, l1.size, l1.top_hubs), depth: 1 })
  }
  if (expandedPath.length > 2) {
    const domainId = expandedPath[0]
    const l1Id = expandedPath[1]
    const l0Id = expandedPath[2]
    const domain = data.domains.find((d) => d.id === domainId)
    const l1List = domain?.communities ?? domain?.subcategories?.flatMap((s) => s.children) ?? []
    const l1 = l1List.find((c) => c.id === l1Id)
    const l0 = l1?.children.find(
      (c): c is L0Community => !("kind" in c) && c.id === l0Id,
    )
    if (l0) crumbs.push({ label: effectiveLabel(l0.label, l0.size, l0.top_hubs), depth: 2 })
  }

  const computedMs = computedAt ? new Date(computedAt).getTime() : undefined

  return (
    <nav
      aria-label="Decomposition breadcrumb"
      className="flex shrink-0 items-center gap-1 border-b border-border/40 bg-card/60 px-3 py-1.5"
    >
      <ol className="flex flex-1 flex-wrap items-center gap-1">
        {crumbs.map((crumb, idx) => (
          <li key={idx} className="flex items-center gap-1">
            {idx > 0 && <ChevronRight className="h-3 w-3 text-muted-foreground/40" aria-hidden="true" />}
            <button
              type="button"
              onClick={() => onCrumbClick(crumb.depth)}
              className={`text-label-xs rounded px-1 py-0.5 transition-colors focus-visible:outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px] ${
                idx === crumbs.length - 1
                  ? "font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {crumb.label}
            </button>
          </li>
        ))}
      </ol>
      {computedMs && (
        <div className="shrink-0 text-label-xs text-muted-foreground">
          <LastUpdated timestamp={computedMs} />
        </div>
      )}
    </nav>
  )
}

// ---------------------------------------------------------------------------
// Main DecompositionIcicle
// ---------------------------------------------------------------------------

export interface DecompositionIcicleProps {
  onInspect?: OnInspect
  onFocusEntity?: OnFocusEntity
  onOpenNeighborhood?: (entityId: string) => void
  /** Search-palette path-walk: entityId to highlight in the tree */
  searchTargetId?: string | null
  /** atlasTier restore — walk to this saved position on mount */
  restoreTier?: AtlasTierPosition | null
  onTierChange?: (tier: AtlasTierPosition) => void
}

export function DecompositionIcicle({
  onInspect,
  onOpenNeighborhood,
  searchTargetId,
  restoreTier,
  onTierChange,
}: DecompositionIcicleProps) {
  const { data, isLoading, isError, error } = useDecomposition()

  const [state, dispatch] = useReducer(icicleReducer, {
    expandedPath: [],
    openEntityList: null,
    domainFilter: "",
    l1Filter: "",
    l0Filter: "",
    entityFilter: "",
  })

  const { msg: announceMsg, announce } = useAnnounce()

  const [tokens, setTokens] = useState(() => getTokens())
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(resolveMapTokens(document.documentElement)))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  // Restore saved tier position on mount
  const restoredRef = useRef(false)
  useEffect(() => {
    if (restoredRef.current || !data || !restoreTier) return
    restoredRef.current = true
    dispatch({ type: "SET_PATH", path: restoreTier.path })
  }, [data, restoreTier])

  // Search-palette path-walk: find the entity's path and expand to it
  const [pulseEntityId, setPulseEntityId] = useState<string | null>(null)
  const pulseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!searchTargetId || !data) return

    // Find the entity's path in the decomposition data by searching community entities
    // The entity path is served in the community payload — we walk parent_map to find l0→l1
    // For the path-walk we find which domain + l1 + l0 the entity belongs to by searching
    // the domain structure. Since entity paths are in the community payload, we use the
    // parent_map to derive the path if we know the l0 id. In this implementation we look
    // for a direct match via the served domains' community structure.
    //
    // The full path walk is triggered by the search palette via `searchTargetId`. When set,
    // we need to determine the domain from community membership. Since the full entity→community
    // mapping is not in the top-level payload (only per-community via ?community=), we
    // walk by domain → l1 → l0 looking for a community that *could* contain this entity.
    // The actual highlighting happens once the entity list for the matching l0 loads.
    //
    // Simplified path: if we already have an expanded path showing this entity, pulse it.
    if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current)
    setPulseEntityId(searchTargetId)
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    if (!prefersReducedMotion) {
      pulseTimerRef.current = setTimeout(() => setPulseEntityId(null), 2000)
    }
  }, [searchTargetId, data])

  // Notify parent of tier changes for A2 atlasTier save
  useEffect(() => {
    onTierChange?.({
      path: state.expandedPath,
      depth: state.expandedPath.length,
    })
  }, [state.expandedPath, onTierChange])

  // Keyboard handling for the icicle wrapper
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        if (e.shiftKey) {
          dispatch({ type: "COLLAPSE_ALL" })
          announce("Collapsed to overview")
        } else {
          dispatch({ type: "COLLAPSE_ONE" })
          announce("Collapsed one tier")
        }
      }
    },
    [announce],
  )

  const handleCrumbClick = useCallback((depth: number) => {
    if (depth === -1) {
      dispatch({ type: "COLLAPSE_ALL" })
    } else {
      dispatch({ type: "SET_PATH", path: state.expandedPath.slice(0, depth + 1) })
    }
  }, [state.expandedPath])

  // 4-state matrix
  if (isLoading) {
    return (
      <div role="region" aria-label="Loading decomposition" className="flex flex-col gap-2 p-3" aria-busy="true">
        <div className="flex gap-2">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-28" />
        </div>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>
            {error instanceof Error ? error.message : "Decomposition failed to load"}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (!data) return null

  if (data.domains.length === 0 && data.uncategorized_count === 0) {
    return (
      <EmptyState
        icon={Layers}
        title="Knowledge backbone not derived yet"
        description="Background processor will build the entity hierarchy automatically."
      />
    )
  }

  const safeTokens = tokens ?? {
    clusters: Array(8).fill("#888") as string[], // drift-allowed: SSR fallback only, never reaches browser
    clusterOther: "#888", // drift-allowed: SSR fallback only, never reaches browser
    domains: Array(12).fill("#888") as string[], // drift-allowed: SSR fallback only, never reaches browser
    domainOther: "#666", // drift-allowed: SSR fallback only, never reaches browser
    edge: "#888", // drift-allowed: SSR fallback only, never reaches browser
    dim: "#888", // drift-allowed: SSR fallback only, never reaches browser
    interaction: "#00C8B4", // drift-allowed: SSR fallback only, never reaches browser
    foreground: "#111", // drift-allowed: SSR fallback only, never reaches browser
    background: "#f5f5f5", // drift-allowed: SSR fallback only, never reaches browser
    trustVerified: "#555", // drift-allowed: SSR fallback only, never reaches browser
    trustPartial: "#777", // drift-allowed: SSR fallback only, never reaches browser
    trustUnverified: "#999", // drift-allowed: SSR fallback only, never reaches browser
    graphite: "#6b7080", // drift-allowed: SSR fallback only, never reaches browser
    grid: "#eee", // drift-allowed: SSR fallback only, never reaches browser
    fontSans: "system-ui, sans-serif",
  }

  const expandedDomainId = state.expandedPath[0] ?? null
  const expandedL1Id = state.expandedPath[1] ?? null
  const expandedL0Id = state.expandedPath[2] ?? null

  return (
    <div
      className="flex h-full flex-col overflow-hidden outline-none"
      onKeyDown={handleKeyDown}
      tabIndex={-1}
      aria-label="Knowledge decomposition"
      role="region"
      data-testid="decomposition-icicle"
    >
      {/* ARIA live region */}
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {announceMsg}
      </div>

      {/* Breadcrumb */}
      <Breadcrumb
        expandedPath={state.expandedPath}
        data={data}
        computedAt={data.computed_at}
        onCrumbClick={handleCrumbClick}
      />

      {/* A3: no communities notice */}
      {data.no_communities_computed && (
        <div className="mx-3 mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
          Clusters appear after the nightly analysis runs. Showing domain overview only.
        </div>
      )}

      {/* Domain filter */}
      <div className="shrink-0 px-3 py-1">
        <FilterInput
          value={state.domainFilter}
          onChange={(v) => dispatch({ type: "SET_DOMAIN_FILTER", value: v })}
          placeholder="Filter domains…"
          depth={-1}
        />
      </div>

      {/* T0 domain rows */}
      <div className="flex-1 overflow-y-auto px-1 pb-2" role="group" aria-label="Domains">
        {data.domains.map((domain) => (
          <DomainSection
            key={domain.id}
            domain={domain}
            expanded={expandedDomainId === domain.id}
            expandedL1Id={expandedDomainId === domain.id ? expandedL1Id : null}
            expandedL0Id={expandedDomainId === domain.id ? expandedL0Id : null}
            pulseEntityId={pulseEntityId}
            domainFilter={state.domainFilter}
            l1Filter={expandedDomainId === domain.id ? state.l1Filter : ""}
            onL1FilterChange={(v) => dispatch({ type: "SET_L1_FILTER", value: v })}
            l0Filter={expandedDomainId === domain.id ? state.l0Filter : ""}
            onL0FilterChange={(v) => dispatch({ type: "SET_L0_FILTER", value: v })}
            entityFilter={state.entityFilter}
            onEntityFilterChange={(v) => dispatch({ type: "SET_ENTITY_FILTER", value: v })}
            tokens={safeTokens}
            onExpand={() => {
              if (expandedDomainId === domain.id) {
                dispatch({ type: "COLLAPSE_ONE" })
                announce(`Collapsed ${domain.label}`)
              } else {
                dispatch({ type: "EXPAND_DOMAIN", domainId: domain.id })
                announce(`Expanded ${domain.label} domain, ${domain.entity_count} entities`)
              }
            }}
            onExpandL1={(l1Id) => {
              const l1List = domain.communities ?? domain.subcategories?.flatMap((s) => s.children) ?? []
              const l1 = l1List.find((c) => c.id === l1Id)
              if (expandedL1Id === l1Id) {
                dispatch({ type: "EXPAND_DOMAIN", domainId: domain.id })
              } else {
                dispatch({ type: "EXPAND_L1", domainId: domain.id, l1Id })
                announce(`Expanded ${l1 ? effectiveLabel(l1.label, l1.size, l1.top_hubs) : l1Id}, community group`)
              }
            }}
            onExpandL0={(l1Id, l0Id) => {
              const l1List = domain.communities ?? domain.subcategories?.flatMap((s) => s.children) ?? []
              const l1 = l1List.find((c) => c.id === l1Id)
              const l0 = l1?.children.find(
                (c): c is L0Community => !("kind" in c) && c.id === l0Id,
              )
              if (expandedL0Id === l0Id) {
                dispatch({ type: "EXPAND_L1", domainId: domain.id, l1Id })
              } else {
                dispatch({ type: "EXPAND_L0", domainId: domain.id, l1Id, l0Id })
                announce(`Expanded ${l0 ? effectiveLabel(l0.label, l0.size, l0.top_hubs) : l0Id}, cluster`)
              }
            }}
            onInspect={onInspect}
            onOpenNeighborhood={onOpenNeighborhood}
            noCommunities={data.no_communities_computed}
            pulseDomain={false}
          />
        ))}

        {/* Uncategorized strip */}
        {data.uncategorized_count > 0 && (
          <div className="flex h-8 items-center gap-2 px-2 text-sm text-muted-foreground/70 mt-1 border-t border-border/20 pt-2">
            <FolderOpen className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="flex-1 truncate">
              Uncategorized ({data.uncategorized_count})
            </span>
          </div>
        )}
      </div>

      {/* Depth indicator */}
      {state.expandedPath.length > 0 && (
        <div className="shrink-0 flex items-center justify-between border-t border-border/20 px-3 py-1 text-label-xs text-muted-foreground">
          <span>Depth {state.expandedPath.length}</span>
          <span className="text-muted-foreground/50">Esc to collapse · Shift+Esc for overview</span>
          <Clock className="h-3 w-3" aria-hidden="true" />
        </div>
      )}
    </div>
  )
}
