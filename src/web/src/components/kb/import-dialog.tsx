// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { scanPreview, startScan, getScanProgress } from "@/lib/api"
import type { ScanPreview } from "@/lib/api/kb"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  FolderOpen, Loader2, CheckCircle2, AlertTriangle, FileText, X,
  HardDrive, Layers, Ban,
} from "lucide-react"
type Phase = "input" | "scanning" | "preview" | "importing" | "complete" | "error"

export function ImportDialog({ onClose }: { onClose: () => void }) {
  const [folderPath, setFolderPath] = useState("/archive")
  const [phase, setPhase] = useState<Phase>("input")
  const [preview, setPreview] = useState<ScanPreview | null>(null)
  const [error, setError] = useState("")
  const [progress, setProgress] = useState<Record<string, unknown> | null>(null)

  const runPreview = useCallback(async () => {
    setPhase("scanning")
    setError("")
    try {
      const result = await scanPreview(folderPath)
      setPreview(result)
      setPhase("preview")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed")
      setPhase("error")
    }
  }, [folderPath])

  const startImport = useCallback(async () => {
    setPhase("importing")
    setError("")
    try {
      const result = await startScan(folderPath)
      // Poll for progress
      const poll = setInterval(async () => {
        try {
          const p = await getScanProgress(result.scan_id)
          setProgress(p)
          if (p.status === "complete" || p.status === "error") {
            clearInterval(poll)
            setPhase(p.status === "complete" ? "complete" : "error")
            if (p.status === "error") setError(String(p.error ?? "Import failed"))
          }
        } catch {
          // Keep polling
        }
      }, 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed")
      setPhase("error")
    }
  }, [folderPath])

  return (
    <Card className="mb-4 border-brand/30">
      <CardHeader className="px-4 pb-2 pt-4">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <FolderOpen className="h-4 w-4 text-brand" />
            Import Folder
          </CardTitle>
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <CardDescription className="text-xs">
          Scan a folder, preview what will be imported, then confirm.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        {/* Phase: Input */}
        {phase === "input" && (
          <>
            <div className="flex gap-2">
              <Input
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                placeholder="/archive or /archive/finance"
                className="text-xs h-8"
              />
              <Button size="sm" className="h-8 shrink-0" onClick={runPreview} disabled={!folderPath.trim()}>
                Scan
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Path relative to the archive mount. Junk files, caches, and system files are automatically skipped.
            </p>
          </>
        )}

        {/* Phase: Scanning */}
        {phase === "scanning" && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Scanning folder structure...
          </div>
        )}

        {/* Phase: Preview */}
        {phase === "preview" && preview && (
          <>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-medium">{preview.total_files}</span>
                <span className="text-muted-foreground">files to import</span>
              </div>
              <div className="flex items-center gap-1.5">
                <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-medium">{preview.total_size_mb} MB</span>
                <span className="text-muted-foreground">total</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Layers className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-medium">~{preview.estimated_chunks.toLocaleString()}</span>
                <span className="text-muted-foreground">chunks</span>
              </div>
              <div className="flex items-center gap-1.5">
                <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-medium">~{preview.estimated_storage_mb} MB</span>
                <span className="text-muted-foreground">storage</span>
              </div>
            </div>

            {/* File types */}
            <div className="flex flex-wrap gap-1">
              {Object.entries(preview.by_extension).slice(0, 8).map(([ext, count]) => (
                <Badge key={ext} variant="secondary" className="text-[10px]">
                  {ext} ({count})
                </Badge>
              ))}
            </div>

            {/* Domains */}
            {Object.keys(preview.by_domain).length > 0 && (
              <div className="flex flex-wrap gap-1">
                {Object.entries(preview.by_domain).map(([domain, count]) => (
                  <Badge key={domain} variant="outline" className="text-[10px]">
                    {domain}: {count}
                  </Badge>
                ))}
              </div>
            )}

            {/* Skipped summary */}
            {(preview.skipped.junk > 0 || preview.skipped.archives > 0 || preview.skipped.unsupported > 0 || preview.skipped.oversized > 0) && (
              <div className="rounded border bg-muted/30 px-2.5 py-1.5 text-[10px] text-muted-foreground space-y-0.5">
                <p className="font-medium flex items-center gap-1">
                  <Ban className="h-3 w-3" />
                  Skipping {preview.skipped.junk + preview.skipped.archives + preview.skipped.unsupported + preview.skipped.oversized} files:
                </p>
                {preview.skipped.junk > 0 && <p>{preview.skipped.junk} junk/system files</p>}
                {preview.skipped.archives > 0 && <p>{preview.skipped.archives} archives (zip/tar)</p>}
                {preview.skipped.unsupported > 0 && <p>{preview.skipped.unsupported} unsupported file types</p>}
                {preview.skipped.oversized > 0 && <p>{preview.skipped.oversized} files over size limit</p>}
              </div>
            )}

            {/* Confirm */}
            <div className="flex gap-2">
              <Button size="sm" className="h-7 text-xs" onClick={startImport}>
                Import {preview.total_files} files
              </Button>
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setPhase("input")}>
                Change folder
              </Button>
              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onClose}>
                Cancel
              </Button>
            </div>
          </>
        )}

        {/* Phase: Importing */}
        {phase === "importing" && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>
                Importing... {String(progress?.files_ingested ?? 0)}/{preview?.total_files ?? "?"} files
              </span>
            </div>
            {progress != null && (
              <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-brand transition-all duration-300"
                  style={{ width: `${Math.min(100, ((Number(progress.files_ingested ?? 0) + Number(progress.files_skipped ?? 0) + Number(progress.files_errored ?? 0)) / Math.max(preview?.total_files ?? 1, 1)) * 100)}%` }}
                />
              </div>
            )}
            {progress?.files_errored != null && Number(progress.files_errored) > 0 && (
              <p className="text-[10px] text-amber-500">{String(progress.files_errored)} errors so far</p>
            )}
          </div>
        )}

        {/* Phase: Complete */}
        {phase === "complete" && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
              <CheckCircle2 className="h-4 w-4" />
              <span className="font-medium">Import complete!</span>
            </div>
            {progress && (
              <div className="text-[11px] text-muted-foreground space-y-0.5">
                <p>Ingested: {String(progress.files_ingested ?? 0)} files</p>
                {Number(progress.files_skipped ?? 0) > 0 && <p>Skipped: {String(progress.files_skipped)} (duplicates/low quality)</p>}
                {Number(progress.files_errored ?? 0) > 0 && <p>Errors: {String(progress.files_errored)}</p>}
              </div>
            )}
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onClose}>
              Done
            </Button>
          </div>
        )}

        {/* Phase: Error */}
        {phase === "error" && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-red-600 dark:text-red-400">
              <AlertTriangle className="h-4 w-4" />
              <span>{error}</span>
            </div>
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setPhase("input")}>
              Try again
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
