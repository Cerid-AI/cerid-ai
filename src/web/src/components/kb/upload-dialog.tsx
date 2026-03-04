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
import { FileUp } from "lucide-react"
import { DOMAINS } from "@/lib/types"

interface UploadDialogProps {
  files: File[]
  defaultDomain: string | null
  onConfirm: (options: { domain?: string; categorize_mode?: string }) => void
  onCancel: () => void
}

export function UploadDialog({ files, defaultDomain, onConfirm, onCancel }: UploadDialogProps) {
  const [domain, setDomain] = useState<string>(defaultDomain ?? "auto")
  const [categorizeMode, setCategorizeMode] = useState("smart")

  const open = files.length > 0

  const handleConfirm = () => {
    onConfirm({
      domain: domain === "auto" ? undefined : domain,
      categorize_mode: categorizeMode,
    })
  }

  const totalSize = files.reduce((sum, f) => sum + f.size, 0)

  return (
    <Dialog open={open} onOpenChange={(isOpen) => { if (!isOpen) onCancel() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileUp className="h-4 w-4" />
            Upload {files.length === 1 ? "File" : `${files.length} Files`}
          </DialogTitle>
          <DialogDescription>
            Choose ingestion options before uploading.
          </DialogDescription>
        </DialogHeader>

        {/* File list */}
        <div className="max-h-32 space-y-1 overflow-y-auto rounded-md border p-2">
          {files.map((file, i) => (
            <div key={i} className="flex items-center justify-between text-xs">
              <span className="min-w-0 truncate text-muted-foreground">{file.name}</span>
              <span className="ml-2 shrink-0 tabular-nums text-[10px] text-muted-foreground/70">
                {formatFileSize(file.size)}
              </span>
            </div>
          ))}
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
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          <Button onClick={handleConfirm}>
            Upload {files.length === 1 ? "File" : `${files.length} Files`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
