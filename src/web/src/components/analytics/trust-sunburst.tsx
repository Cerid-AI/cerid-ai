// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Trust score sunburst — Phase L Day 1.
//
// Two-ring radial chart built from concentric recharts Pies.
//   center  : composite score with band color
//   ring 1  : 6 components, segment angle = equal weight, color = status
//
// Backend `/observability/trust-score` doesn't yet expose per-component
// sub-metric history (audit confirmed), so ring 2 is deferred. The
// component renders the existing data faithfully + cross-links to the
// TrustScoreModal for the detail drill.

import { useState } from "react"
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Loader2, ShieldCheck } from "lucide-react"
import { useTrustScore } from "@/hooks/use-trust-score"
import { TrustScoreModal } from "@/components/trust-score/trust-score-modal"
import { getBandDisplay, type ComponentStatus } from "@/lib/types/trust-score"
import { cn } from "@/lib/utils"

const STATUS_COLOR: Record<ComponentStatus, string> = {
  ok: "var(--chart-ok)",
  warn: "var(--chart-warn)",
  fail: "var(--chart-fail)",
  not_available: "var(--chart-neutral)",
}

const BAND_COLOR: Record<string, string> = {
  high: "var(--chart-ok)",
  medium: "var(--chart-warn)",
  low: "var(--chart-fail)",
  unavailable: "var(--chart-neutral)",
}

export function TrustSunburst() {
  const { data, isLoading, error, refetch } = useTrustScore()
  const [modalOpen, setModalOpen] = useState(false)

  if (isLoading) {
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground h-72">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading trust score…
      </Card>
    )
  }

  if (error || !data) {
    return (
      <Card className="p-4 border-amber-500/30 bg-amber-500/5">
        <div className="text-sm text-amber-600" role="alert">
          Trust score unavailable.
          <Button variant="link" size="sm" onClick={() => refetch()}>
            retry
          </Button>
        </div>
      </Card>
    )
  }

  const bandKey = data.band ?? "unavailable"
  const bandColor = BAND_COLOR[bandKey]
  const bandDisplay = getBandDisplay(data.band)

  // Each component gets an equal slice of the outer ring. Color reflects
  // status (not value) so failing components are immediately visible
  // regardless of relative magnitude.
  const componentSlices = data.components.map((c) => ({
    name: c.label,
    id: c.id,
    value: 1,
    fill: STATUS_COLOR[c.status],
    component: c,
  }))

  return (
    <>
      <Card className="p-4" data-testid="trust-sunburst">
        <div className="flex items-start justify-between mb-2">
          <div>
            <h3 className="text-base font-semibold flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" />
              Trust Score
            </h3>
            <p className="text-xs text-muted-foreground">
              Composite of 6 quality signals.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setModalOpen(true)}
            data-testid="trust-sunburst-drill"
          >
            Drill down
          </Button>
        </div>

        <div className="relative h-72">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              {/* Outer ring — components by status color */}
              <Pie
                data={componentSlices}
                dataKey="value"
                cx="50%"
                cy="50%"
                innerRadius="62%"
                outerRadius="86%"
                paddingAngle={2}
                stroke="none"
              >
                {componentSlices.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Pie>
              {/* Center ring — single segment colored by band */}
              <Pie
                data={[{ name: "score", value: 1 }]}
                dataKey="value"
                cx="50%"
                cy="50%"
                innerRadius="0%"
                outerRadius="56%"
                stroke="none"
              >
                <Cell fill={bandColor} fillOpacity={0.18} />
              </Pie>
              <Tooltip
                formatter={(_v, _n, ctx) => {
                  // ctx.payload carries the original slice object
                  const slice = (ctx as { payload?: typeof componentSlices[0] })?.payload
                  if (!slice?.component) return [""]
                  const c = slice.component
                  const valStr = c.value == null
                    ? "n/a"
                    : c.value.toFixed(2)
                  return [
                    `${valStr} · ${c.status}`,
                    c.label,
                  ]
                }}
              />
            </PieChart>
          </ResponsiveContainer>
          {/* Center label */}
          <div
            className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none"
            data-testid="trust-sunburst-center"
          >
            <span className={cn("text-4xl font-bold tabular-nums", bandDisplay.textClass)}>
              {data.score ?? "–"}
            </span>
            <span className={cn("text-xs uppercase tracking-wide", bandDisplay.textClass)}>
              {bandDisplay.label}
            </span>
          </div>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-x-3 gap-y-1 pt-2 text-xs text-muted-foreground">
          {componentSlices.map((s) => (
            <span key={s.id} className="flex items-center gap-1">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: s.fill }} // drift-allowed: runtime chart-series color swatch
              />
              {s.name}
            </span>
          ))}
        </div>
      </Card>

      <TrustScoreModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        data={data}
      />
    </>
  )
}
