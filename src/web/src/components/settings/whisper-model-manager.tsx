// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ProgressBar } from "@/components/ui/progress-bar"
import { Check, Download, Loader2, Trash2, X } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  fetchWhisperModels,
  startWhisperDownload,
  getWhisperDownloadStatus,
  cancelWhisperDownload,
  deleteWhisperModel,
  type WhisperModelInfo,
  type WhisperDownloadStatus,
} from "@/lib/api/settings"

interface ActiveDownload {
  modelId: string
  status: WhisperDownloadStatus
}

export function WhisperModelManager() {
  const [models, setModels] = useState<WhisperModelInfo[]>([])
  const [cacheDir, setCacheDir] = useState<string>("")
  const [defaultModel, setDefaultModel] = useState<string>("")
  const [active, setActive] = useState<Record<string, ActiveDownload>>({})
  const [error, setError] = useState<string | null>(null)
  const pollersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  const refresh = useCallback(async () => {
    try {
      const list = await fetchWhisperModels()
      setModels(list.models)
      setCacheDir(list.cache_dir)
      setDefaultModel(list.current_default)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load models")
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Clean up all pollers on unmount
  useEffect(() => {
    const pollers = pollersRef.current
    return () => {
      Object.values(pollers).forEach((id) => clearInterval(id))
    }
  }, [])

  const handleDownload = useCallback(async (modelId: string) => {
    try {
      const { download_id } = await startWhisperDownload(modelId)
      setActive((prev) => ({
        ...prev,
        [modelId]: {
          modelId,
          status: {
            download_id,
            model_id: modelId,
            state: "pending",
            bytes_downloaded: 0,
            bytes_total: null,
            error: null,
          },
        },
      }))

      const poll = setInterval(async () => {
        try {
          const s = await getWhisperDownloadStatus(download_id)
          setActive((prev) => ({ ...prev, [modelId]: { modelId, status: s } }))
          if (s.state === "completed" || s.state === "failed" || s.state === "cancelled") {
            clearInterval(poll)
            delete pollersRef.current[modelId]
            await refresh()
            if (s.state !== "completed") {
              // Keep the row visible briefly so the user sees the final state
              setTimeout(() => {
                setActive((prev) => {
                  const next = { ...prev }
                  delete next[modelId]
                  return next
                })
              }, 3000)
            } else {
              setActive((prev) => {
                const next = { ...prev }
                delete next[modelId]
                return next
              })
            }
          }
        } catch (e) {
          clearInterval(poll)
          delete pollersRef.current[modelId]
          setError(e instanceof Error ? e.message : "Polling failed")
        }
      }, 500)
      pollersRef.current[modelId] = poll
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed to start")
    }
  }, [refresh])

  const handleCancel = useCallback(async (modelId: string) => {
    const a = active[modelId]
    if (!a) return
    try {
      await cancelWhisperDownload(a.status.download_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed")
    }
  }, [active])

  const handleDelete = useCallback(async (modelId: string) => {
    try {
      await deleteWhisperModel(modelId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed")
    }
  }, [refresh])

  return (
    <div className="space-y-3" data-testid="whisper-model-manager">
      <div>
        <h3 className="text-lg font-semibold">Whisper Models</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Choose a speech-to-text model. Higher quality = slower + larger.
          Cache: <code className="text-xs">{cacheDir || "…"}</code>
        </p>
      </div>

      {error && (
        <div className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5" role="alert">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {models.map((m) => {
          const isDefault = m.id === defaultModel
          const dl = active[m.id]
          const downloading = dl?.status.state === "downloading" || dl?.status.state === "pending"
          const pct = dl && dl.status.bytes_total
            ? Math.round((dl.status.bytes_downloaded / dl.status.bytes_total) * 100)
            : 0

          return (
            <Card
              key={m.id}
              className={cn(
                "p-3 space-y-2",
                isDefault && "border-blue-500/40 bg-blue-500/5"
              )}
              data-testid={`whisper-model-row-${m.id}`}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{m.id}</span>
                    {isDefault && (
                      <span className="text-xs bg-blue-500/20 text-blue-600 px-1.5 py-0.5 rounded">
                        default
                      </span>
                    )}
                    {m.cached && (
                      <span className="inline-flex items-center gap-1 text-xs text-green-600">
                        <Check className="w-3 h-3" /> cached
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">{m.description}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {m.size_mb} MB · {m.quality} quality · ~{(m.rtf_estimate * 60).toFixed(1)} min/hr audio
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  {m.cached && !downloading && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(m.id)}
                      data-testid={`whisper-delete-${m.id}`}
                      aria-label={`Delete cached ${m.id}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  )}
                  {!m.cached && !downloading && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleDownload(m.id)}
                      data-testid={`whisper-download-${m.id}`}
                    >
                      <Download className="w-4 h-4 mr-1" />
                      Download
                    </Button>
                  )}
                  {downloading && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleCancel(m.id)}
                      data-testid={`whisper-cancel-${m.id}`}
                    >
                      <X className="w-4 h-4 mr-1" />
                      Cancel
                    </Button>
                  )}
                </div>
              </div>

              {dl && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      {dl.status.state === "downloading" || dl.status.state === "pending"
                        ? <Loader2 className="w-3 h-3 animate-spin" />
                        : null}
                      {dl.status.state}
                    </span>
                    <span>
                      {dl.status.bytes_total
                        ? `${(dl.status.bytes_downloaded / 1024 / 1024).toFixed(1)} / ${(dl.status.bytes_total / 1024 / 1024).toFixed(1)} MB (${pct}%)`
                        : `${(dl.status.bytes_downloaded / 1024 / 1024).toFixed(1)} MB`}
                    </span>
                  </div>
                  {dl.status.bytes_total !== null && (
                    <ProgressBar pct={pct} size="sm" />
                  )}
                  {dl.status.error && (
                    <p className="text-xs text-red-500">{dl.status.error}</p>
                  )}
                </div>
              )}
            </Card>
          )
        })}
      </div>
    </div>
  )
}
