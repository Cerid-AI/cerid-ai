// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Analytics panel — Phase L Day 3.5.
//
// Composes the four Phase L visualizations + handles cross-link
// navigation (heatmap → Sources Activity with date filter).

import { useCallback } from "react"
import { TrustSunburst } from "./trust-sunburst"
import { GrowthHeatmap } from "./growth-heatmap"
import { CostSankey } from "./cost-sankey"
import { QualityTimeline } from "./quality-timeline"
import { KnowledgePanel } from "./knowledge-panel"

interface AnalyticsPanelProps {
  tier?: string
}

export function AnalyticsPanel({ tier = "community" }: AnalyticsPanelProps) {
  const onHeatmapClick = useCallback((date: string) => {
    // Deep-link to Sources → Activity with the date filter. The
    // Activity stream doesn't yet narrow on a `since` param (Phase L
    // tracks the activity-side filter as deferred), but the URL state
    // is the correct contract — we'll wire the receiver in a small
    // follow-up.
    const url = new URL(window.location.href)
    url.searchParams.set("pane", "sources")
    url.searchParams.set("sources_mode", "activity")
    url.searchParams.set("since", date)
    window.history.pushState({}, "", url.toString())
    window.dispatchEvent(new PopStateEvent("popstate"))
  }, [])

  return (
    <div className="space-y-3" data-testid="analytics-panel">
      {/* Row 1: trust + growth — both free-tier visible */}
      <div className="grid gap-3 md:grid-cols-2">
        <TrustSunburst />
        <GrowthHeatmap onCellClick={onHeatmapClick} />
      </div>
      {/* Row 2: cost Sankey — Pro */}
      <CostSankey tier={tier} />
      {/* Row 3: quality timeline — Pro */}
      <QualityTimeline tier={tier} />
      {/* Row 4: knowledge architecture metrics (Phase K6.2) — free tier */}
      <KnowledgePanel />
    </div>
  )
}
