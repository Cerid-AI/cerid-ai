// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Wiki provenance Sankey — Phase M Day 5 (revised).
//
// Visualizes the chain of evidence for an entity's wiki page: source
// artifacts → entity. Built from the source_artifacts array already
// present on the WikiEntityPage — no additional fetch required.
//
// The original design bucketed edges by `r.attestation`, but that field
// has no writer anywhere in the backend (all edges default to "inferred"
// via coalesce). The chart is rebuilt from real data: source artifact
// titles + per-MENTIONS confidence values, grouped by source_type.

import { useMemo } from "react"
import {
  Sankey,
  Tooltip,
  ResponsiveContainer,
  Rectangle,
  Layer,
} from "recharts"
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { cn } from "@/lib/utils"
import { communitySlot } from "@/components/subjects/timeline/stratigraph/strata-layout"
import { resolveMapTokens } from "@/components/subjects/constellation/map/community-layer"
import type { SourceCitation } from "@/lib/types/wiki"
import { useState } from "react"

interface ProvenanceSankeyProps {
  entitySlug: string
  entityName?: string
  /** Community ID of the focal entity, for the entity terminus hue. */
  communityId?: string | null
  /** Source artifacts from the wiki page — the real data source for this chart. */
  sourceArtifacts: SourceCitation[]
  /** Click → opens the full Atlas with the entity focused + provenance lens on. */
  onOpenAtlas?: (slug: string) => void
}

export function ProvenanceSankey({
  entitySlug,
  entityName,
  communityId,
  sourceArtifacts,
  onOpenAtlas,
}: ProvenanceSankeyProps) {
  const [expanded, setExpanded] = useState(false)

  const sankeyData = useMemo(() => {
    if (sourceArtifacts.length === 0) return null

    // Group sources by source_type (or "file" as default).
    const groups = new Map<string, { count: number; totalConf: number }>()
    for (const src of sourceArtifacts) {
      const key = src.source_type ?? "file"
      const existing = groups.get(key) ?? { count: 0, totalConf: 0 }
      groups.set(key, {
        count: existing.count + 1,
        totalConf: existing.totalConf + (src.confidence ?? 0.5),
      })
    }

    if (groups.size === 0) return null

    // Namespace node keys by column (src / mid / dst) so a group name that
    // collides with "Sources" or the terminal entity name maps to a distinct
    // node instead of collapsing onto one index (zero-width self-loop).
    const terminalName = entityName ?? entitySlug
    const nodes = [
      { name: "Sources", key: "src:Sources" },
      ...Array.from(groups.keys()).map((g) => ({ name: g, key: `mid:${g}` })),
      { name: terminalName, key: `dst:${terminalName}` },
    ]
    const nodeIndex = new Map(nodes.map((n, i) => [n.key, i]))
    const links = [
      ...Array.from(groups.entries()).map(([group, stats]) => ({
        source: nodeIndex.get("src:Sources") ?? 0,
        target: nodeIndex.get(`mid:${group}`) ?? 0,
        value: stats.count,
      })),
      ...Array.from(groups.entries()).map(([group, stats]) => ({
        source: nodeIndex.get(`mid:${group}`) ?? 0,
        target: nodeIndex.get(`dst:${terminalName}`) ?? 0,
        value: stats.count,
        avgConfidence: stats.totalConf / stats.count,
      })),
    ]
    return { nodes, links, sourceCount: sourceArtifacts.length, entityName: terminalName }
  }, [sourceArtifacts, entityName, entitySlug])

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
          {/* empty state — no source artifacts recorded */}
          {!sankeyData && (
            <EmptyState
              icon={GitBranch}
              title="No sources recorded"
              description={`No source artifacts are linked to ${entityName ?? entitySlug} yet.`}
            />
          )}
          {sankeyData && (
            <>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <Sankey
                    data={sankeyData}
                    nodeWidth={8}
                    nodePadding={14}
                    linkCurvature={0.5}
                    iterations={32}
                    margin={{ left: 80, right: 80, top: 8, bottom: 8 }}
                    node={<SankeyNode communityId={communityId} entityNodeName={sankeyData.entityName} />}
                    link={{ stroke: "currentColor", className: "text-border/60", strokeOpacity: 0.5 }}
                  >
                    <Tooltip
                      formatter={((value: number) => [`${value}`, "sources"]) as never}
                    />
                  </Sankey>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
                <span>{sankeyData.sourceCount} source{sankeyData.sourceCount !== 1 ? "s" : ""} recorded</span>
                {onOpenAtlas && (
                  <Button
                    variant="link"
                    size="sm"
                    onClick={() => onOpenAtlas(entitySlug)}
                    data-testid="provenance-sankey-open-atlas"
                    className={cn("h-auto py-0 text-xs")}
                  >
                    Open in Atlas →
                  </Button>
                )}
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
  payload?: { name?: string; sourceLinks?: unknown[]; targetLinks?: unknown[] }
  /** Community ID of the focal entity — drives hue on the entity terminus node */
  communityId?: string | null
  /** Name of the entity terminus node so we can identify it */
  entityNodeName?: string
}

function SankeyNode(props: SankeyNodeProps) {
  const { x = 0, y = 0, width = 8, height = 0, payload, communityId, entityNodeName } = props
  const name = payload?.name

  // A node is a left-side label when it has source links (it sends flow outward).
  // A node is a right-side label when it has target links and no source links (it receives flow).
  // Middle nodes have both: label goes right (away from incoming links).
  const hasSourceLinks = (payload?.sourceLinks?.length ?? 0) > 0
  const isOrigin = hasSourceLinks && (payload?.targetLinks?.length ?? 0) === 0
  const labelRight = !isOrigin

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

  // Clamp label length to prevent overflow inside the SVG bounds.
  const maxChars = 12
  const label = name && name.length > maxChars ? `${name.slice(0, maxChars)}…` : (name ?? "")

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
        x={labelRight ? x + width + 4 : x - 4}
        y={y + height / 2}
        textAnchor={labelRight ? "start" : "end"}
        dominantBaseline="middle"
        className="text-label-xxs fill-foreground"
      >
        {label}
      </text>
    </Layer>
  )
}

