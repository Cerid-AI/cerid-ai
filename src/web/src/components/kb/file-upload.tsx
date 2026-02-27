// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback, useEffect } from "react"
import { Upload, CheckCircle, AlertCircle, Loader2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { uploadFile, fetchSupportedExtensions } from "@/lib/api"
import { DOMAINS } from "@/lib/types"
import type { UploadResult } from "@/lib/types"

type UploadState = "idle" | "dragging" | "uploading" | "complete" | "error"

interface FileUploadProps {
  onUploadComplete?: (result: UploadResult) => void
}

export function FileUpload({ onUploadComplete }: FileUploadProps) {
  const [state, setState] = useState<UploadState>("idle")
  const [domain, setDomain] = useState<string>("")
  const [extensions, setExtensions] = useState<string[]>([])
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string>("")
  const [currentFile, setCurrentFile] = useState<string>("")
  const [queue, setQueue] = useState<File[]>([])
  const [queueIndex, setQueueIndex] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetchSupportedExtensions()
      .then((data) => setExtensions(data.extensions))
      .catch(() => setExtensions([]))
  }, [])

  const processFile = useCallback(
    async (file: File) => {
      setState("uploading")
      setCurrentFile(file.name)
      setError("")
      setResult(null)

      try {
        const uploadResult = await uploadFile(file, {
          domain: domain || undefined,
        })
        setResult(uploadResult)
        setState("complete")
        onUploadComplete?.(uploadResult)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed")
        setState("error")
      }
    },
    [domain, onUploadComplete],
  )

  const processQueue = useCallback(
    async (files: File[]) => {
      for (let i = 0; i < files.length; i++) {
        setQueueIndex(i)
        await processFile(files[i])
      }
      setQueue([])
      setQueueIndex(0)
    },
    [processFile],
  )

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return
      const fileArray = Array.from(files)
      setQueue(fileArray)
      processQueue(fileArray)
    },
    [processQueue],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setState((prev) => (prev === "uploading" ? prev : "dragging"))
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setState((prev) => (prev === "uploading" ? prev : "idle"))
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setState("idle")
      handleFiles(e.dataTransfer.files)
    },
    [handleFiles],
  )

  const handleRetry = useCallback(() => {
    if (queue.length > 0 && queueIndex < queue.length) {
      processFile(queue[queueIndex])
    }
  }, [queue, queueIndex, processFile])

  const handleReset = useCallback(() => {
    setState("idle")
    setResult(null)
    setError("")
    setCurrentFile("")
    setQueue([])
    setQueueIndex(0)
  }, [])

  const extensionDisplay =
    extensions.length > 0
      ? extensions.slice(0, 12).join(", ") +
        (extensions.length > 12 ? ` +${extensions.length - 12} more` : "")
      : "Loading..."

  return (
    <div className="space-y-3">
      {/* Domain selector */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Domain:</span>
        <Select value={domain} onValueChange={setDomain}>
          <SelectTrigger size="sm" className="w-40">
            <SelectValue placeholder="Auto-detect" />
          </SelectTrigger>
          <SelectContent>
            {DOMAINS.map((d) => (
              <SelectItem key={d} value={d}>
                <span className="capitalize">{d}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {domain && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setDomain("")}
            className="text-muted-foreground"
            aria-label="Clear domain selection"
          >
            <X className="h-3 w-3" />
          </Button>
        )}
      </div>

      {/* Drop zone */}
      <Card
        className={cn(
          "cursor-pointer border-2 border-dashed transition-colors",
          state === "dragging" && "border-primary bg-primary/5",
          state === "idle" && "border-muted-foreground/25 hover:border-muted-foreground/50",
          (state === "uploading" || state === "complete" || state === "error") &&
            "cursor-default border-muted-foreground/25",
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => {
          if (state === "idle" || state === "dragging") {
            fileInputRef.current?.click()
          }
        }}
      >
        <CardContent className="flex min-h-[200px] flex-col items-center justify-center p-6">
          {state === "idle" || state === "dragging" ? (
            <>
              <Upload
                className={cn(
                  "mb-3 h-10 w-10",
                  state === "dragging"
                    ? "text-primary"
                    : "text-muted-foreground",
                )}
              />
              <p className="text-sm font-medium">
                Drop files here or click to browse
              </p>
              <p className="mt-1 text-center text-xs text-muted-foreground">
                {extensionDisplay}
              </p>
              {queue.length > 1 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Multiple files supported
                </p>
              )}
            </>
          ) : state === "uploading" ? (
            <>
              <Loader2 className="mb-3 h-10 w-10 animate-spin text-primary" />
              <p className="text-sm font-medium">Uploading...</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {currentFile}
              </p>
              {queue.length > 1 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  File {queueIndex + 1} of {queue.length}
                </p>
              )}
            </>
          ) : state === "complete" && result ? (
            <>
              <CheckCircle className="mb-3 h-10 w-10 text-green-500" />
              <p className="text-sm font-medium">Upload complete</p>
              <div className="mt-2 flex flex-wrap items-center justify-center gap-1.5">
                <Badge variant="outline" className="text-xs capitalize">
                  {result.domain}
                </Badge>
                <Badge variant="secondary" className="text-xs">
                  {result.chunks} chunks
                </Badge>
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                {result.filename}
              </p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">
                {result.artifact_id}
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="mt-3"
                onClick={(e) => {
                  e.stopPropagation()
                  handleReset()
                }}
              >
                Upload another
              </Button>
            </>
          ) : state === "error" ? (
            <>
              <AlertCircle className="mb-3 h-10 w-10 text-destructive" />
              <p className="text-sm font-medium">Upload failed</p>
              <p className="mt-1 max-w-xs text-center text-xs text-destructive">
                {error}
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleRetry()
                  }}
                >
                  Retry
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleReset()
                  }}
                >
                  Dismiss
                </Button>
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        aria-label="Upload files"
        accept={extensions.map((ext) => `.${ext}`).join(",")}
        onChange={(e) => {
          handleFiles(e.target.files)
          // Reset input so the same file can be re-selected
          e.target.value = ""
        }}
      />
    </div>
  )
}