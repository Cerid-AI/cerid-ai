// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, lazy, Suspense } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2, AlertCircle, FileText, Code2, Table2, FileType } from "lucide-react"
import { DomainBadge } from "./domain-filter"
import { fetchArtifactDetail } from "@/lib/api"
import { getFileRenderMode, getLanguageFromFilename } from "@/lib/utils"
import type { ArtifactDetail } from "@/lib/types"

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

export function ArtifactPreview({ artifactId, open, onClose }: ArtifactPreviewProps) {
  const [detail, setDetail] = useState<ArtifactDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !artifactId) return
    setLoading(true)
    setError(null)
    setDetail(null)

    fetchArtifactDetail(artifactId)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load artifact"))
      .finally(() => setLoading(false))
  }, [open, artifactId])

  const renderMode = detail ? getFileRenderMode(detail.filename) : "text"
  const language = detail ? getLanguageFromFilename(detail.filename) : "text"
  const ModeIcon = MODE_ICONS[renderMode]

  return (
    <Dialog open={open} onOpenChange={(isOpen) => { if (!isOpen) onClose() }}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 truncate">
            <ModeIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
            {detail?.filename ?? "Loading..."}
          </DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-2">
              {detail && (
                <>
                  <DomainBadge domain={detail.domain} />
                  <Badge variant="secondary" className="text-xs">
                    {detail.chunk_count} chunk{detail.chunk_count !== 1 ? "s" : ""}
                  </Badge>
                  <span className="text-xs text-muted-foreground capitalize">
                    {renderMode}
                  </span>
                </>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="min-h-0 flex-1 rounded-md border bg-muted/30">
          <div className="p-4">
            {loading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading content...
              </div>
            )}

            {error && (
              <div className="flex flex-col items-center gap-2 py-16 text-center">
                <AlertCircle className="h-8 w-8 text-destructive" />
                <p className="text-sm text-destructive">{error}</p>
              </div>
            )}

            {detail && !loading && !error && (
              <>
                {renderMode === "code" ? (
                  <Suspense fallback={<pre className="whitespace-pre-wrap text-xs">{detail.total_content}</pre>}>
                    <SyntaxHighlighter language={language}>
                      {detail.total_content}
                    </SyntaxHighlighter>
                  </Suspense>
                ) : (
                  <pre className="whitespace-pre-wrap text-xs leading-relaxed [overflow-wrap:anywhere]">
                    {detail.total_content}
                  </pre>
                )}
              </>
            )}

            {detail && !loading && !error && !detail.total_content && (
              <p className="py-8 text-center text-sm text-muted-foreground italic">
                No content available
              </p>
            )}
          </div>
        </ScrollArea>

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  )
}

export default ArtifactPreview
