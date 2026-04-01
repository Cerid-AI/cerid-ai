// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, lazy, Suspense } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Loader2, AlertCircle, FileText, Code2, Table2, FileType, Layers, Calendar, ChevronDown, ChevronUp, X } from "lucide-react"
import { DomainBadge } from "@/components/ui/domain-badge"
import { SourceTypeBadge } from "./source-type-badge"
import { QualityDot } from "./quality-dot"
import { fetchArtifactDetail, updateArtifactTags } from "@/lib/api"
import { getFileRenderMode, getLanguageFromFilename } from "@/lib/utils"

const SyntaxHighlighter = lazy(() =>
  import("@/lib/syntax-highlighter").then((mod) => ({
    default: ({ language, children }: { language: string; children: string }) => (
      <mod.PrismLight language={language} style={mod.oneDark} customStyle={{ margin: 0, borderRadius: "0.375rem", fontSize: "0.75rem" }}>
        {children}
      </mod.PrismLight>
    ),
  }))
)

interface ArtifactPreviewProps {
  artifactId: string
  open: boolean
  onClose: () => void
}

const MODE_ICONS = {
  code: Code2,
  markdown: FileText,
  table: Table2,
  text: FileType,
} as const

const PREVIEW_CHARS = 500

export function ArtifactPreview({ artifactId, open, onClose }: ArtifactPreviewProps) {
  const [showFull, setShowFull] = useState(false)
  const [editingTags, setEditingTags] = useState(false)
  const [tagInput, setTagInput] = useState("")
  const [editedTags, setEditedTags] = useState<string[]>([])
  const [savingTags, setSavingTags] = useState(false)

  const { data: detail, isLoading: loading, error: queryError } = useQuery({
    queryKey: ["artifact-detail", artifactId],
    queryFn: () => fetchArtifactDetail(artifactId),
    enabled: open && !!artifactId,
    staleTime: 60_000,
  })

  const error = queryError ? (queryError instanceof Error ? queryError.message : "Failed to load artifact") : null
  const renderMode = detail ? getFileRenderMode(detail.filename) : "text"
  const language = detail ? getLanguageFromFilename(detail.filename) : "text"
  const ModeIcon = MODE_ICONS[renderMode]

  const contentPreview = detail?.total_content
    ? showFull
      ? detail.total_content
      : detail.total_content.slice(0, PREVIEW_CHARS)
    : ""
  const hasMore = (detail?.total_content?.length ?? 0) > PREVIEW_CHARS

  const sourceType = detail?.source_type
  const metadata = detail?.metadata ?? {}
  const qualityScore = typeof metadata.quality_score === "number" ? metadata.quality_score : undefined
  const totalTokens = typeof metadata.total_tokens === "number" ? metadata.total_tokens : undefined
  const tags: string[] = Array.isArray(metadata.tags)
    ? metadata.tags.filter((t): t is string => typeof t === "string")
    : []

  const handleSaveTags = async () => {
    setSavingTags(true)
    try {
      await updateArtifactTags(artifactId, editedTags)
      setEditingTags(false)
    } finally {
      setSavingTags(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(isOpen) => { if (!isOpen) { onClose(); setShowFull(false) } }}>
      <SheetContent className="flex flex-col w-full sm:max-w-lg p-0">
        {/* Header */}
        <div className="flex items-start gap-3 border-b px-4 py-3">
          <ModeIcon className="h-5 w-5 shrink-0 text-muted-foreground mt-0.5" />
          <div className="min-w-0 flex-1">
            <SheetTitle className="truncate text-sm font-semibold">
              {detail?.filename ?? "Loading..."}
            </SheetTitle>
            <SheetDescription asChild>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {detail && (
                  <>
                    <DomainBadge domain={detail.domain} />
                    {sourceType && <SourceTypeBadge sourceType={sourceType} />}
                    <QualityDot score={qualityScore} />
                  </>
                )}
              </div>
            </SheetDescription>
          </div>
        </div>

        {/* Scrollable body */}
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-4">
            {/* Loading */}
            {loading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading content...
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="flex flex-col items-center gap-2 py-16 text-center">
                <AlertCircle className="h-8 w-8 text-destructive" />
                <p className="text-sm text-destructive">{error}</p>
              </div>
            )}

            {/* Metadata grid */}
            {detail && !loading && !error && (
              <>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div>
                    <p className="text-muted-foreground mb-0.5">Domain</p>
                    <p className="font-medium capitalize">{detail.domain}</p>
                  </div>
                  {detail.metadata?.sub_category != null && (
                    <div>
                      <p className="text-muted-foreground mb-0.5">Sub-category</p>
                      <p className="font-medium">{String(detail.metadata.sub_category)}</p>
                    </div>
                  )}
                  {qualityScore != null && (
                    <div>
                      <p className="text-muted-foreground mb-0.5">Quality Score</p>
                      <p className="flex items-center gap-1.5 font-medium">
                        <QualityDot score={qualityScore} />
                        {Math.round(qualityScore * 100)}%
                      </p>
                    </div>
                  )}
                  <div>
                    <p className="text-muted-foreground mb-0.5">Chunks</p>
                    <p className="flex items-center gap-1 font-medium">
                      <Layers className="h-3 w-3" />
                      {detail.chunk_count}
                    </p>
                  </div>
                  {totalTokens != null && (
                    <div>
                      <p className="text-muted-foreground mb-0.5">Total Tokens</p>
                      <p className="font-medium">{totalTokens.toLocaleString()}</p>
                    </div>
                  )}
                  {sourceType && (
                    <div>
                      <p className="text-muted-foreground mb-0.5">Source</p>
                      <SourceTypeBadge sourceType={sourceType} />
                    </div>
                  )}
                  {detail.metadata?.ingested_at != null && (
                    <div>
                      <p className="text-muted-foreground mb-0.5">Ingested</p>
                      <p className="flex items-center gap-1 font-medium">
                        <Calendar className="h-3 w-3" />
                        {new Date(String(detail.metadata.ingested_at)).toLocaleDateString()}
                      </p>
                    </div>
                  )}
                </div>

                {/* Tags */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">Tags</p>
                    <Button
                      variant="ghost"
                      size="xs"
                      className="h-5 text-[10px]"
                      onClick={() => {
                        if (editingTags) {
                          setEditingTags(false)
                        } else {
                          setEditedTags(tags)
                          setTagInput("")
                          setEditingTags(true)
                        }
                      }}
                    >
                      {editingTags ? "Cancel" : "Edit"}
                    </Button>
                  </div>
                  {editingTags ? (
                    <div className="rounded border bg-muted/30 p-2">
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
                          variant="default"
                          size="xs"
                          className="h-6 text-[10px]"
                          disabled={savingTags}
                          onClick={handleSaveTags}
                        >
                          {savingTags ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
                        </Button>
                      </div>
                    </div>
                  ) : tags.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {tags.map((tag) => (
                        <Badge key={tag} variant="secondary" className="text-[10px]">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs italic text-muted-foreground">No tags</p>
                  )}
                </div>

                <Separator />

                {/* Content preview */}
                <div>
                  <p className="text-xs text-muted-foreground mb-2">Content Preview</p>
                  {renderMode === "code" ? (
                    <Suspense fallback={<pre className="whitespace-pre-wrap text-xs">{contentPreview}</pre>}>
                      <SyntaxHighlighter language={language}>
                        {contentPreview}
                      </SyntaxHighlighter>
                    </Suspense>
                  ) : (
                    <pre className="whitespace-pre-wrap text-xs leading-relaxed [overflow-wrap:anywhere] rounded-md border bg-muted/30 p-3">
                      {contentPreview}
                      {!showFull && hasMore && "..."}
                    </pre>
                  )}
                  {hasMore && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-1 h-7 text-xs"
                      onClick={() => setShowFull(!showFull)}
                    >
                      {showFull ? (
                        <><ChevronUp className="mr-1 h-3 w-3" /> Show less</>
                      ) : (
                        <><ChevronDown className="mr-1 h-3 w-3" /> View full</>
                      )}
                    </Button>
                  )}
                </div>

                {!detail.total_content && (
                  <p className="py-8 text-center text-sm text-muted-foreground italic">
                    No content available
                  </p>
                )}
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

export default ArtifactPreview
