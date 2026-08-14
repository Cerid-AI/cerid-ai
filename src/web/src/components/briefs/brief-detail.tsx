// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * BriefDetail — labelled sections + claim verification for a single brief.
 *
 * Claims render through the canonical `<VerifiedResponse>` component via
 * `briefClaimToFE` (lib/briefs/adapt.ts) — this is the first production
 * caller of VerifiedResponse outside the chat/audit surfaces.
 */

import { ArrowLeft, MessageSquare, TriangleAlert } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import { LastUpdated } from "@/components/ui/last-updated"
import { VerifiedResponse } from "@/components/verification/verified-response"
import { useNavigation } from "@/contexts/navigation-context"
import { briefClaimToFE } from "@/lib/briefs/adapt"
import type { Brief } from "@/lib/types/brief"

interface BriefDetailProps {
  brief: Brief
  onBack: () => void
}

function sectionSlug(title: string, i: number): string {
  const base = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  return `brief-section-${base || i}`
}

export function BriefDetail({ brief, onBack }: BriefDetailProps) {
  const { goTo, composeChat } = useNavigation()

  const dateLabel = new Date(brief.generated_at).toLocaleDateString(undefined, {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  })

  const handleDiscuss = () => {
    composeChat({ text: `Discuss the ${brief.kind} brief from ${dateLabel}` })
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <Button variant="ghost" size="sm" className="-ml-2 gap-1.5" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to briefs
        </Button>
        <LastUpdated timestamp={Date.parse(brief.generated_at)} />
      </div>

      <div>
        <h2 className="text-2xl font-semibold capitalize text-foreground">{brief.kind} brief</h2>
        <p className="mt-1 text-sm text-muted-foreground">{dateLabel}</p>
        {brief.stale && (
          <p className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-600 dark:text-amber-400">
            <TriangleAlert className="h-3.5 w-3.5" aria-hidden="true" />
            Stale — {brief.new_items_since_generation ?? "new"} items landed after this brief was generated
          </p>
        )}
      </div>

      <div className="space-y-8">
        {brief.sections.map((section, i) => {
          const headingId = sectionSlug(section.title, i)
          return (
            <section key={headingId} aria-labelledby={headingId}>
              <h3 id={headingId} className="text-lg font-semibold text-foreground">
                {section.title}
              </h3>
              <div className="prose prose-sm dark:prose-invert mt-2 max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.body}</ReactMarkdown>
              </div>
            </section>
          )
        })}
      </div>

      {brief.claims.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-muted-foreground">Claims in this brief</h3>
          <VerifiedResponse
            claims={brief.claims.map(briefClaimToFE)}
            onArtifactClick={() => goTo("sources")}
          />
        </div>
      )}

      <div className="border-t pt-4">
        <Button variant="outline" className="gap-1.5" onClick={handleDiscuss}>
          <MessageSquare className="h-4 w-4" aria-hidden="true" />
          Discuss this brief
        </Button>
      </div>
    </div>
  )
}
