// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Interactive Leiden community explorer (Phase R.2).
 *
 * Design choice: **inline panel** — community list on the left, detail view
 * on the right.  Chosen over a Dialog because this is a primary browse
 * surface (like the wiki entity list), not a transient overlay.
 *
 * Layout:
 *   ┌─────────────────┬────────────────────────────────────┐
 *   │  Community list │  Community detail                  │
 *   │  (cards)        │  synthesis + member entities       │
 *   │                 │  + "Ask about this community" CTA  │
 *   └─────────────────┴────────────────────────────────────┘
 *
 * Interactions:
 * - Click a card → loads detail on the right (keyboard: Enter / Space)
 * - Member entity pill → `props.onEntityClick(canonical_id)` (TODO: wire to wiki route)
 * - "Ask about this community" → `props.onAskAbout(community)` (TODO: prefill chat)
 * - Entity list in detail is collapsed by default; expanded on demand.
 */

import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  Network,
  Users,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  AlertCircle,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { ScrollArea } from "@/components/ui/scroll-area"
import { EmptyState } from "@/components/ui/empty-state"

import { useCommunities, useCommunity } from "@/hooks/use-communities"
import type { CommunitySummary, CommunityFull } from "@/lib/types/community"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GraphExplorerProps {
  /**
   * Called when the user clicks a member-entity pill.
   * TODO(R.2-link): wire to the wiki entity route to navigate to the entity page.
   */
  onEntityClick?: (canonical_id: string) => void
  /**
   * Called when the user clicks "Ask about this community".
   * The parent should use `community.summary` to prefill a chat query.
   * TODO(R.2-link): wire to chat state / query prefill.
   */
  onAskAbout?: (community: CommunityFull) => void
}

// ---------------------------------------------------------------------------
// Community list card
// ---------------------------------------------------------------------------

function CommunityCard({
  community,
  isSelected,
  onClick,
}: {
  community: CommunitySummary
  isSelected: boolean
  onClick: () => void
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      aria-label={`Community ${community.community_id}, ${community.member_count} members`}
      className={[
        "cursor-pointer rounded-lg border p-3 text-sm transition-colors",
        "hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        isSelected ? "border-brand bg-brand/5" : "border-border bg-card",
      ].join(" ")}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onClick()
        }
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="truncate text-xs font-medium text-foreground">
            {community.summary
              ? community.summary.slice(0, 72) + (community.summary.length > 72 ? "…" : "")
              : `Community ${community.community_id}`}
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="secondary" className="gap-1 text-[10px]">
              <Users className="h-2.5 w-2.5" aria-hidden="true" />
              {community.member_count}
            </Badge>
            <Badge variant="outline" className="font-mono text-[10px]">
              L{community.level}
            </Badge>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Community detail panel
// ---------------------------------------------------------------------------

function MemberEntityList({
  members,
  onEntityClick,
}: {
  members: CommunityFull["members"]
  onEntityClick?: (canonical_id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? members : members.slice(0, 6)
  const hasMore = members.length > 6

  return (
    <section aria-labelledby="community-members-heading">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-left"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls="community-members-list"
      >
        <h2
          id="community-members-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Entities ({members.length})
        </h2>
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {expanded && (
        <div id="community-members-list" className="mt-2 flex flex-wrap gap-1.5">
          {visible.map((m) => (
            <Button
              key={m.canonical_id}
              variant="outline"
              size="sm"
              className="h-auto rounded-full px-2.5 py-0.5 text-xs"
              aria-label={`${m.name} (${m.entity_type})`}
              onClick={() => {
                // TODO(R.2-link): wire to wiki entity route
                console.log(`TODO(R.2-link): navigate to wiki entity ${m.canonical_id}`)
                onEntityClick?.(m.canonical_id)
              }}
            >
              {m.name}
            </Button>
          ))}
          {hasMore && !expanded && (
            <span className="self-center text-[10px] text-muted-foreground">
              +{members.length - 6} more
            </span>
          )}
        </div>
      )}
    </section>
  )
}

function CommunityDetailPanel({
  communityId,
  onEntityClick,
  onAskAbout,
}: {
  communityId: string
  onEntityClick?: (canonical_id: string) => void
  onAskAbout?: (community: CommunityFull) => void
}) {
  const { data, isLoading, isError } = useCommunity(communityId)

  if (isLoading) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading community details"
        className="space-y-4 p-6"
      >
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
        <div className="flex flex-wrap gap-2 pt-2">
          <Skeleton className="h-6 w-16 rounded-full" />
          <Skeleton className="h-6 w-20 rounded-full" />
          <Skeleton className="h-6 w-14 rounded-full" />
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription>Failed to load community. Please try again.</AlertDescription>
        </Alert>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-5 p-6">
        {/* Header */}
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-base font-semibold text-foreground">
              Community {data.community_id}
            </h1>
            <Badge variant="secondary" className="gap-1 text-[10px]">
              <Users className="h-2.5 w-2.5" aria-hidden="true" />
              {data.member_count} entities
            </Badge>
            <Badge variant="outline" className="font-mono text-[10px]">
              Level {data.level}
            </Badge>
          </div>
          {data.last_summarized_at && (
            <p className="text-xs text-muted-foreground">
              Synthesized {_relativeTime(data.last_summarized_at)}
            </p>
          )}
        </div>

        <Separator />

        {/* Synthesis */}
        {data.summary && (
          <section aria-labelledby="community-synthesis-heading">
            <h2
              id="community-synthesis-heading"
              className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Synthesis
            </h2>
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.summary}</ReactMarkdown>
            </div>
          </section>
        )}

        {/* Member entities (lazy-collapsed) */}
        {data.members.length > 0 && (
          <MemberEntityList members={data.members} onEntityClick={onEntityClick} />
        )}

        {/* Ask about this community CTA */}
        <Button
          variant="default"
          size="sm"
          className="w-full gap-2"
          aria-label="Ask about this community"
          onClick={() => {
            // TODO(R.2-link): prefill chat query with community.summary as context
            console.log(`TODO(R.2-link): prefill chat with community ${data.community_id}`)
            onAskAbout?.(data)
          }}
        >
          <MessageSquare className="h-4 w-4" aria-hidden="true" />
          Ask about this community
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Interactive community explorer.
 *
 * Renders a two-column layout:
 * - Left: scrollable list of community cards
 * - Right: detail panel for the selected community
 *
 * Selection approach: inline panel.  See file docstring for rationale.
 */
export function GraphExplorer({ onEntityClick, onAskAbout }: GraphExplorerProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data: communities, isLoading, isError } = useCommunities()

  if (isLoading) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading communities"
        className="space-y-2 p-4"
      >
        <Skeleton className="h-16 w-full rounded-lg" />
        <Skeleton className="h-16 w-full rounded-lg" />
        <Skeleton className="h-16 w-full rounded-lg" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription>Failed to load communities. Please try again.</AlertDescription>
        </Alert>
      </div>
    )
  }

  if (!communities || communities.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          icon={Network}
          title="No communities yet"
          description="Communities are detected from your corpus. Ingest more documents and run the background refresh."
        />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0">
      {/* ------------------------------------------------------------------ */}
      {/* Left: community list                                                */}
      {/* ------------------------------------------------------------------ */}
      <section
        className="w-64 shrink-0 border-r"
        aria-label="Community list"
      >
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <Network className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-xs font-semibold text-muted-foreground">
            Communities ({communities.length})
          </h2>
        </div>
        <ScrollArea className="h-[calc(100%-44px)]">
          <ul className="space-y-1.5 p-3">
            {communities.map((c) => (
              <li key={c.community_id}>
                <CommunityCard
                  community={c}
                  isSelected={selectedId === c.community_id}
                  onClick={() => setSelectedId(c.community_id)}
                />
              </li>
            ))}
          </ul>
        </ScrollArea>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Right: detail panel                                                */}
      {/* ------------------------------------------------------------------ */}
      <section className="min-w-0 flex-1" aria-label="Community detail">
        {selectedId ? (
          <CommunityDetailPanel
            communityId={selectedId}
            onEntityClick={onEntityClick}
            onAskAbout={onAskAbout}
          />
        ) : (
          <div className="flex h-full items-center justify-center p-8 text-center">
            <div className="space-y-1">
              <Network className="mx-auto h-8 w-8 text-muted-foreground/40" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                Select a community to explore its synthesis and entities
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _relativeTime(iso: string): string {
  try {
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return ""
    const seconds = Math.floor((Date.now() - ms) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return ""
  }
}
