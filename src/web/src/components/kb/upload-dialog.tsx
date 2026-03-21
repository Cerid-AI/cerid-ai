// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { FileUp, Layers, Lock, CheckCircle, Loader2, AlertCircle } from "lucide-react"
import { DOMAINS } from "@/lib/types"
import { formatFileSize } from "@/lib/utils"
import { cn } from "@/lib/utils"

const BATCH_THRESHOLD = 3

const FILE_TYPE_ICONS: Record<string, string> = {
  ".pdf": "PDF",
  ".docx": "DOCX",
  ".doc": "DOC",
  ".csv": "CSV",
  ".txt": "TXT",
  ".json": "JSON",
  ".md": "MD",
  ".epub": "EPUB",
  ".eml": "EML",
  ".html": "HTML",
  ".htm": "HTML",
  ".xlsx": "XLSX",
  ".xls": "XLS",
  ".mp3": "MP3",
  ".wav": "WAV",
  ".png": "PNG",
  ".jpg": "JPG",
  ".jpeg": "JPEG",
  ".webp": "WEBP",
  ".gif": "GIF",
}

/** Extensions that require pro tier for processing. */
const PRO_EXTENSIONS = new Set([".mp3", ".wav", ".png", ".jpg", ".jpeg", ".webp", ".gif"])

function getFileExtension(filename: string): string {
  const idx = filename.lastIndexOf(".")
  return idx >= 0 ? filename.slice(idx).toLowerCase() : ""
}

function getFileTypeLabel(filename: string): string {
  const ext = getFileExtension(filename)
  return FILE_TYPE_ICONS[ext] ?? ext.replace(".", "").toUpperCase() || "FILE"
}

function isProType(filename: string): boolean {
  return PRO_EXTENSIONS.has(getFileExtension(filename))
}

export type FileUploadStatus = "pending" | "uploading" | "success" | "error"

interface UploadDialogProps {
  files: File[]
  defaultDomain: string | null
  onConfirm: (options: { domain?: string; categorize_mode?: string }) => void
  onCancel: () => void
  /** Optional tier — when "community", pro file types show a lock icon. */
  tier?: string
  /** Optional per-file upload status for progress display. */
  fileStatuses?: FileUploadStatus[]
  /** Whether upload is in progress. */
  uploading?: boolean
}

export function UploadDialog({ files, defaultDomain, onConfirm, onCancel, tier, fileStatuses, uploading }: UploadDialogProps) {
  const [domain, setDomain] = useState<string>(defaultDomain ?? "auto")
  const [categorizeMode, setCategorizeMode] = useState("smart")

  const open = files.length > 0
  const isBatch = files.length >= BATCH_THRESHOLD
  const isCommunity = !tier || tier === "community"

  const handleConfirm = () => {
    onConfirm({
      domain: domain === "auto" ? undefined : domain,
      categorize_mode: categorizeMode,
    })
  }

  const totalSize = files.reduce((sum, f) => sum + f.size, 0)

  return (
    <Dialog open={open} onOpenChange={(isOpen) => { if (!isOpen) onCancel() }}>
      <DialogContent className={isBatch ? "sm:max-w-lg" : "sm:max-w-md"}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isBatch ? (
              <>
                <Layers className="h-4 w-4" />
                Batch Upload — {files.length} Files
              </>
            ) : (
              <>
                <FileUp className="h-4 w-4" />
                Upload {files.length === 1 ? "File" : `${files.length} Files`}
              </>
            )}
          </DialogTitle>
          <DialogDescription>
            {isBatch
              ? `${files.length} files (${formatFileSize(totalSize)}) will be uploaded in parallel.`
              : "Choose ingestion options before uploading."}
          </DialogDescription>
        </DialogHeader>

        {/* File list */}
        <div className={`space-y-1 overflow-y-auto rounded-md border p-2 ${isBatch ? "max-h-48" : "max-h-32"}`}>
          {files.map((file, i) => {
            const status = fileStatuses?.[i]
            return (
              <div key={i} className="flex items-center justify-between gap-1.5 text-xs">
                <div className="flex min-w-0 items-center gap-1.5">
                  {/* File status indicator */}
                  {status === "uploading" && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />}
                  {status === "success" && <CheckCircle className="h-3 w-3 shrink-0 text-green-500" />}
                  {status === "error" && <AlertCircle className="h-3 w-3 shrink-0 text-destructive" />}
                  <span className="min-w-0 truncate text-muted-foreground">{file.name}</span>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Badge
                    variant="secondary"
                    className={cn(
                      "text-[9px] px-1.5 py-0",
                      isProType(file.name) && isCommunity && "border-amber-500/40 text-amber-600 dark:text-amber-400",
                    )}
                  >
                    {isProType(file.name) && isCommunity && (
                      <Lock className="mr-0.5 h-2.5 w-2.5" />
                    )}
                    {getFileTypeLabel(file.name)}
                  </Badge>
                  <span className="tabular-nums text-[10px] text-muted-foreground/70">
                    {formatFileSize(file.size)}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
        {files.length > 1 && (
          <p className="text-[10px] text-muted-foreground">
            Total: {formatFileSize(totalSize)}
          </p>
        )}

        {/* Options */}
        <div className="grid gap-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Domain</span>
            <Select value={domain} onValueChange={setDomain}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">
                  <span className="flex items-center gap-1.5">
                    Auto-detect
                    <Badge variant="outline" className="text-[9px]">AI</Badge>
                  </span>
                </SelectItem>
                {DOMAINS.map((d) => (
                  <SelectItem key={d} value={d} className="capitalize">
                    {d}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Categorization</span>
            <Select value={categorizeMode} onValueChange={setCategorizeMode}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="manual">Manual</SelectItem>
                <SelectItem value="smart">Smart (Free)</SelectItem>
                <SelectItem value="pro">Pro (Paid)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={uploading}>Cancel</Button>
          <Button onClick={handleConfirm} disabled={uploading}>
            {uploading ? (
              <>
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                Uploading...
              </>
            ) : isBatch ? (
              `Start Batch (${files.length})`
            ) : (
              `Upload ${files.length === 1 ? "File" : `${files.length} Files`}`
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
