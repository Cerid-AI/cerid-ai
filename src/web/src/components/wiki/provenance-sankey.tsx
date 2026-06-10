// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Wiki provenance Sankey — Phase M Day 5.
//
// Visualizes the chain of evidence for an entity's wiki page: source
// artifacts → claim type → entity. Uses recharts Sankey (already in
// the dependency tree for Phase L's cost flow viz).
//
// Reuses the existing `/graph/neighborhood` endpoint — the artifacts
// that mention this entity are already in its 1-hop neighborhood. We
// extract them client-side rather than adding a new endpoint.

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Sankey,
  Tooltip,
  ResponsiveContainer,
  Rectangle,
  Layer,
} from "recharts"
import { AlertCircle, ChevronDown, ChevronRight, GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { EmptyState } from "@/components/ui/empty-state"
import { fetchNeighborhood } from "@/lib/api/graph"
import type { NeighborhoodResponse } from "@/lib/types/graph"
import { cn } from "@/lib/utils"
import { communitySlot } from "@/components/subjects/timeline/stratigraph/strata-layout"
import { resolveMapTokens } from "@/components/subjects/constellation/map/community-layer"

interface ProvenanceSankeyProps {
  entitySlug: string
  entityName?: string
  /** Community ID of the focal entity, for the entity terminus hue. */
  communityId?: string | null
  /** Click → opens the full Atlas with the entity focused + provenance lens on. */
  onOpenAtlas?: (slug: string) => void
}

export function ProvenanceSankey({
  entitySlug,
  entityName,
  communityId,
  onOpenAtlas,
}: ProvenanceSankeyProps) {
  const [expanded, setExpanded] = useState(false)
  const [data, setData] = useState<NeighborhoodResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!expanded || data || loading) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setLoading(true)
    fetchNeighborhood(entitySlug, 1)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false))
  }, [expanded, entitySlug, data, loading])

  const sankeyData = useMemo(() => {
    if (!data) return null
    const focal = data.focal_entity

    const buckets = new Map<string, number>()
    let unattested = 0
    for (const edge of data.edges) {
      if (edge.source !== focal && edge.target !== focal) continue
      const bucket = edge.attestation || "unknown"
      buckets.set(bucket, (buckets.get(bucket) ?? 0) + 1)
      if (bucket === "inferred") unattested += 1
    }

    if (buckets.size === 0) return null

    const nodes = [
      { name: "Sources" },
      ...Array.from(buckets.keys()).map((b) => ({ name: b })),
      { name: entityName ?? focal },
    ]
    const nodeIndex = new Map(nodes.map((n, i) => [n.name, i]))
    const links = [
      ...Array.from(buckets.entries()).map(([bucket, count]) => ({
        source: nodeIndex.get("Sources") ?? 0,
        target: nodeIndex.get(bucket) ?? 0,
        value: count,
      })),
      ...Array.from(buckets.entries()).map(([bucket, count]) => ({
        source: nodeIndex.get(bucket) ?? 0,
        target: nodeIndex.get(entityName ?? focal) ?? 0,
        value: count,
      })),
    ]
    return { nodes, links, unattested, entityName: entityName ?? focal }
  }, [data, entityName])

  const handleOpenAtlas = useCallback(() => {
    onOpenAtlas?.(entitySlug)
  }, [entitySlug, onOpenAtlas])

  return (
    <section aria-labelledby="wiki-provenance-sankey-heading" data-testid="provenance-sankey">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
        aria-expanded={expanded}
        aria-controls="wiki-provenance-sankey-body"
        id="wiki-provenance-sankey-heading"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3" aria-hidden="true" />
        ) : (
          <ChevronRight className="w-3 h-3" aria-hidden="true" />
        )}
        <GitBranch className="w-3 h-3" aria-hidden="true" />
        Provenance Flow
      </button>

      {expanded && (
        <div
          id="wiki-provenance-sankey-body"
          className="mt-2 rounded-md border border-border bg-card/50 p-3"
        >
          {loading && (
            <div className="space-y-2 py-2" role="status" aria-label="Loading provenance data">
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-4/5" />
              <Skeleton className="h-8 w-full" />
            </div>
          )}
          {error && (
            <Alert variant="destructive" className="py-2">
              <AlertCircle className="h-3.5 w-3.5" />
              <AlertDescription className="text-xs">{error}</AlertDescription>
            </Alert>
          )}
          {data && (!sankeyData || sankeyData.nodes.length < 3) && (
            <EmptyState
              icon={GitBranch}
              title="No attestation recorded"
              description={`No source attestation recorded for ${entityName ?? entitySlug} yet.`}
            />
          )}
          {sankeyData && sankeyData.nodes.length >= 3 && (
            <>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <Sankey
                    data={sankeyData}
                    nodeWidth={8}
                    nodePadding={14}
                    linkCurvature={0.5}
                    iterations={32}
                    node={<SankeyNode communityId={communityId} entityNodeName={sankeyData.entityName} />}
                    link={{ stroke: "currentColor", className: "text-border/60", strokeOpacity: 0.5 }}
                  >
                    <Tooltip
                      formatter={((value: number) => [`${value}`, "edges"]) as never}
                    />
                  </Sankey>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
                <span>
                  {sankeyData.links.length / 2} attestation buckets
                  {sankeyData.unattested > 0 && (
                    <> · <span className="text-destructive">{sankeyData.unattested} inferred</span></>
                  )}
                </span>
                <Button
                  variant="link"
                  size="sm"
                  onClick={handleOpenAtlas}
                  data-testid="provenance-sankey-open-atlas"
                  className={cn("h-auto py-0 text-xs")}
                >
                  Open in Atlas →
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}


// Node renderer with side-aware label placement + community-hued entity terminus.
interface SankeyNodeProps {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: { name?: string; sourceLinks?: unknown[] }
  /** Community ID of the focal entity — drives hue on the entity terminus node */
  communityId?: string | null
  /** Name of the entity terminus node so we can identify it */
  entityNodeName?: string
}

function SankeyNode(props: SankeyNodeProps) {
  const { x = 0, y = 0, width = 8, height = 0, payload, communityId, entityNodeName } = props
  const isLeft = (payload?.sourceLinks?.length ?? 0) > 0
  const name = payload?.name

  // Entity terminus gets community hue; all other nodes use neutral foreground/40.
  const isEntityNode = name === entityNodeName
  let fillColor = "currentColor"
  let fillOpacity = 0.35

  if (isEntityNode && communityId) {
    // drift-allowed: runtime token resolution — community color resolved from CSS tokens
    const tokens = resolveMapTokens(document.documentElement)
    const slot = communitySlot(communityId)
    fillColor = tokens.clusters[slot] ?? tokens.clusterOther
    fillOpacity = 0.75
  }

  return (
    <Layer>
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        fill={fillColor}
        fillOpacity={fillOpacity}
      />
      <text
        x={isLeft ? x - 4 : x + width + 4}
        y={y + height / 2}
        textAnchor={isLeft ? "end" : "start"}
        dominantBaseline="middle"
        className="text-[9px] fill-foreground"
      >
        {name}
      </text>
    </Layer>
  )
}
