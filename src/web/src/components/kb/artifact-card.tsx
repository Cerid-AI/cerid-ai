// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { PlusCircle, GitBranch, Globe, ArrowRightLeft, Loader2, X, Eye, Trash2, Tags, Check, RefreshCw, Layers } from "lucide-react"
import { DomainBadge } from "@/components/ui/domain-badge"
import type { KBQueryResult } from "@/lib/types"
import { cn } from "@/lib/utils"

/** Touch device detection — pointer type is static per device, so module-level is fine. */
const isTouchDevice = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches

/** Returns a human-readable relative time string like "3d ago", "2h ago", "5m ago". */
function timeAgo(date: string): string {
  const now = Date.now()
  const then = new Date(date).getTime()
  if (isNaN(then)) return ""
  const diffMs = now - then
  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return "just now"
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  const years = Math.floor(months / 12)
  return `${years}y ago`
}

interface ArtifactCardProps {
  result: KBQueryResult
  isSelected: boolean
  onSelect: () => void
  onInject: () => void
  domains?: string[]
  onRecategorize?: (artifactId: string, newDomain: string) => Promise<void>
  onPreview?: (artifactId: string) => void
  onDelete?: (artifactId: string) => Promise<void>
  onUpdateTags?: (artifactId: string, tags: string[]) => Promise<void>
  onReIngest?: (artifactId: string) => Promise<void>
  /** When true, show the client_source badge on each card. */
  showSource?: boolean
}

export function ArtifactCard({ result, isSelected, onSelect, onInject, domains, onRecategorize, onPreview, onDelete, onUpdateTags, onReIngest, showSource }: ArtifactCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [showRecategorize, setShowRecategorize] = useState(false)
  const [recategorizing, setRecategorizing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editingTags, setEditingTags] = useState(false)
  const [tagInput, setTagInput] = useState("")
  const [editedTags, setEditedTags] = useState<string[]>([])
  const [savingTags, setSavingTags] = useState(false)
  const [reIngesting, setReIngesting] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)

  // Scroll card into view when expanding via card click
  const prevExpanded = useRef(expanded)
  if (expanded && !prevExpanded.current) {
    requestAnimationFrame(() => {
      cardRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" })
    })
  }
  prevExpanded.current = expanded
  const relevancePct = Math.round(result.relevance * 100)
  const showRelevance = result.relevance > 0
  const isBrowseMode = result.relevance === 0
  // Clean up garbled OCR/form text: collapse whitespace, strip control chars, trim trailing truncation
  const cleanContent = result.content
    // eslint-disable-next-line no-control-regex -- intentional: strip control chars from OCR/form text
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "")
    .replace(/\s+/g, " ")
    .replace(/[|]{2,}/g, "")
    .trim()
    .replace(/[-–]\s*$/, "...")

  // Metadata from result — chunk_count comes through when using artifactToResult
  const metadata = (result as unknown as Record<string, unknown>)
  const chunkCount = typeof metadata.chunk_count === "number" ? metadata.chunk_count : undefined
  const clientSource = typeof metadata.client_source === "string" ? metadata.client_source : undefined

  return (
    <Card
      ref={cardRef}
      className={cn(
        "w-full min-w-0 max-w-full cursor-pointer overflow-hidden transition-colors",
        isSelected && "ring-2 ring-primary",
      )}
      role="button"
      tabIndex={0}
      aria-label={`${result.filename} - ${result.domain}`}
      draggable={!isTouchDevice}
      onDragStart={isTouchDevice ? undefined : (e) => {
        e.dataTransfer.setData("application/cerid-artifact", JSON.stringify({
          artifact_id: result.artifact_id,
          filename: result.filename,
          domain: result.domain,
          content: result.content,
          relevance: result.relevance,
          sub_category: result.sub_category,
          tags: result.tags,
          quality_score: result.quality_score,
          chunk_index: result.chunk_index,
        }))
        e.dataTransfer.effectAllowed = "copy"
      }}
      onClick={() => {
        setExpanded((prev) => !prev)
        onSelect()
      }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect() } }}
    >
      <CardContent className="min-w-0 overflow-hidden p-3">
        {/* Header row */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <p className="min-w-0 truncate text-sm font-medium">{result.filename}</p>
              {chunkCount != null && (
                <Badge variant="outline" className="shrink-0 gap-0.5 text-[9px] px-1.5 py-0">
                  <Layers className="h-2.5 w-2.5" />
                  {chunkCount}
                </Badge>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <DomainBadge domain={result.domain} />
              {result.ingested_at && (
                <span className="text-[10px] text-muted-foreground" title={new Date(result.ingested_at).toLocaleString()}>
                  {timeAgo(result.ingested_at)}
                </span>
              )}
              {result.graph_source && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <GitBranch className="h-3 w-3" />
                  graph
                </Badge>
              )}
              {result.cross_domain && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Globe className="h-3 w-3" />
                  cross
                </Badge>
              )}
              {result.sub_category && result.sub_category !== "general" && (
                <Badge variant="secondary" className="text-[10px]">
                  {result.sub_category}
                </Badge>
              )}
              {/* Client source badge — shown when showSource is true and source is non-gui */}
              {showSource && clientSource && clientSource !== "gui" && (
                <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-teal-500/40 text-teal-600 dark:text-teal-400">
                  {clientSource}
                </Badge>
              )}
            </div>
            {result.tags && result.tags.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {result.tags.slice(0, 4).map((tag) => (
                  <span key={tag} className="inline-flex items-center truncate max-w-[120px] rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {tag}
                  </span>
                ))}
                {result.tags.length > 4 && (
                  <span className="text-[10px] text-muted-foreground">+{result.tags.length - 4}</span>
                )}
              </div>
            )}
          </div>
          <TooltipProvider delayDuration={0}>
            <div className="flex flex-col items-end gap-1">
              {showRelevance && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex flex-col items-end gap-0.5">
                      <span className="text-xs font-medium tabular-nums">{relevancePct}%</span>
                      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{ width: `${relevancePct}%` }}
                        />
                      </div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>Relevance: {relevancePct}% match to query</TooltipContent>
                </Tooltip>
              )}
              {result.quality_score != null && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span><QualityBadge score={result.quality_score} /></span>
                  </TooltipTrigger>
                  <TooltipContent>Quality: Q{Math.round(result.quality_score * 100)} — higher is better</TooltipContent>
                </Tooltip>
              )}
            </div>
          </TooltipProvider>
        </div>

        {/* Content */}
        {cleanContent.length > 10 ? (
          <p className={cn(
            "mt-2 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]",
            !expanded && "line-clamp-2",
          )}>
            {expanded ? result.content : (result.summary || cleanContent)}
          </p>
        ) : result.summary ? (
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground line-clamp-2 [overflow-wrap:anywhere]">
            {result.summary}
          </p>
        ) : isBrowseMode ? (
          <p className="mt-2 text-xs italic text-muted-foreground/60">
            No summary available
          </p>
        ) : null}

        {/* Recategorize inline picker */}
        {showRecategorize && domains && onRecategorize && (
          <div className="mt-2 flex flex-wrap items-center gap-1 rounded border bg-muted/30 p-2">
            <span className="text-[10px] text-muted-foreground">Move to:</span>
            {domains.filter((d) => d !== result.domain).map((d) => (
              <Button
                key={d}
                variant="outline"
                size="xs"
                className="h-5 text-[10px] capitalize"
                disabled={recategorizing}
                onClick={async (e) => {
                  e.stopPropagation()
                  setRecategorizing(true)
                  try {
                    await onRecategorize(result.artifact_id, d)
                    setShowRecategorize(false)
                  } finally {
                    setRecategorizing(false)
                  }
                }}
              >
                {d}
              </Button>
            ))}
            {recategorizing && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
            <Button variant="ghost" size="xs" className="ml-auto h-5 w-5 p-0" onClick={(e) => { e.stopPropagation(); setShowRecategorize(false) }}>
              <X className="h-3 w-3" />
            </Button>
          </div>
        )}

        {/* Inline tag editing */}
        {editingTags && onUpdateTags && (
          <div className="mt-2 rounded border bg-muted/30 p-2" onClick={(e) => e.stopPropagation()}>
            <div className="flex flex-wrap gap-1 mb-1.5">
              {editedTags.map((tag) => (
                <span key={tag} className="inline-flex items-center gap-0.5 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                  {tag}
                  <button className="hover:text-destructive" onClick={() => setEditedTags((t) => t.filter((x) => x !== tag))}>
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <input
                className="h-6 flex-1 rounded border bg-background px-1.5 text-[11px] outline-none focus:ring-1 focus:ring-primary"
                placeholder="Add tag..."
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && tagInput.trim()) {
                    e.preventDefault()
                    const newTag = tagInput.trim().toLowerCase()
                    if (!editedTags.includes(newTag)) setEditedTags((t) => [...t, newTag])
                    setTagInput("")
                  }
                }}
              />
              <Button
                variant="ghost"
                size="xs"
                className="h-6 text-[10px] text-primary"
                disabled={savingTags}
                onClick={async () => {
                  setSavingTags(true)
                  try {
                    await onUpdateTags(result.artifact_id, editedTags)
                    setEditingTags(false)
                  } finally {
                    setSavingTags(false)
                  }
                }}
              >
                {savingTags ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                Save
              </Button>
              <Button variant="ghost" size="xs" className="h-6 text-[10px]" onClick={() => setEditingTags(false)}>
                <X className="h-3 w-3" />
              </Button>
            </div>
          </div>
        )}

        {/* Delete confirmation */}
        {confirmDelete && onDelete && (
          <div className="mt-2 flex items-center gap-2 rounded border border-destructive/30 bg-destructive/10 p-2" onClick={(e) => e.stopPropagation()}>
            <span className="text-[11px] text-destructive">Delete this artifact?</span>
            <div className="flex-1" />
            <Button
              variant="destructive"
              size="xs"
              className="h-5 text-[10px]"
              disabled={deleting}
              onClick={async () => {
                setDeleting(true)
                try { await onDelete(result.artifact_id) } finally { setDeleting(false); setConfirmDelete(false) }
              }}
            >
              {deleting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
              Delete
            </Button>
            <Button variant="ghost" size="xs" className="h-5 text-[10px]" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </div>
        )}

        {/* Actions */}
        <div className="mt-2 flex items-center gap-0.5">
          {domains && onRecategorize && (
            <Button
              variant="ghost"
              size="icon"
              className="artifact-action-btn h-6 w-6"
              onClick={(e) => {
                e.stopPropagation()
                setShowRecategorize(!showRecategorize)
              }}
              title="Move to another domain"
            >
              <ArrowRightLeft className="h-3 w-3" />
            </Button>
          )}
          {onPreview && (
            <Button
              variant="ghost"
              size="icon"
              className="artifact-action-btn h-6 w-6"
              onClick={(e) => {
                e.stopPropagation()
                onPreview(result.artifact_id)
              }}
              title="Preview content"
            >
              <Eye className="h-3 w-3" />
            </Button>
          )}
          {onUpdateTags && (
            <Button
              variant="ghost"
              size="icon"
              className="artifact-action-btn h-6 w-6"
              onClick={(e) => {
                e.stopPropagation()
                setEditedTags(result.tags ?? [])
                setTagInput("")
                setEditingTags(!editingTags)
              }}
              title="Edit tags"
            >
              <Tags className="h-3 w-3" />
            </Button>
          )}
          {onReIngest && (
            <Button
              variant="ghost"
              size="icon"
              className="artifact-action-btn h-6 w-6"
              disabled={reIngesting}
              onClick={async (e) => {
                e.stopPropagation()
                setReIngesting(true)
                try {
                  await onReIngest(result.artifact_id)
                } finally {
                  setReIngesting(false)
                }
              }}
              title="Re-ingest artifact"
            >
              {reIngesting ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
            </Button>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              size="icon"
              className="artifact-action-btn h-6 w-6 hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation()
                setConfirmDelete(!confirmDelete)
              }}
              title="Delete artifact"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          )}
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="icon"
            className="artifact-action-btn h-6 w-6 text-primary"
            onClick={(e) => {
              e.stopPropagation()
              onInject()
            }}
            title="Inject into chat context"
          >
            <PlusCircle className="h-3 w-3" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function QualityBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const tier =
    score >= 0.8 ? { label: "excellent", color: "border-green-500/50 text-green-700 dark:text-green-400" } :
    score >= 0.6 ? { label: "good", color: "border-blue-500/50 text-blue-700 dark:text-blue-400" } :
    score >= 0.4 ? { label: "fair", color: "border-yellow-500/50 text-yellow-700 dark:text-yellow-400" } :
                   { label: "poor", color: "border-red-500/50 text-red-700 dark:text-red-400" }

  return (
    <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0", tier.color)}>
      Q{pct}
    </Badge>
  )
}
