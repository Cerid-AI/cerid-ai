// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { FileText, Upload, Sparkles, Loader2, Check, Send } from "lucide-react"
import { cn } from "@/lib/utils"
import { uploadFile, queryKB } from "@/lib/api"
import { useDragDrop } from "@/hooks/use-drag-drop"

interface FirstDocState {
  ingested: boolean
  queried: boolean
  skipped: boolean
}

interface FirstDocumentStepProps {
  state: FirstDocState
  onChange: (state: FirstDocState) => void
}

type Phase = "choose" | "ingesting" | "chat" | "done"

const SUGGESTION_CHIPS = [
  "What is this document about?",
  "Summarize the key points",
  "What topics does it cover?",
]

const ACCEPTED_EXTS = ".pdf,.txt,.md,.docx"

// Distinguish "backend actually unreachable" from "backend rejected this file"
// so users get an actionable error rather than a wild goose chase. Network
// errors throw without a status; HTTP errors throw with the body in the
// message (see extractError in lib/api/common.ts).
function formatIngestError(err: unknown, filename: string): string {
  if (err instanceof TypeError) {
    // fetch threw — genuine network failure, backend is unreachable
    return `Cerid backend unreachable at ${filename}. Check that the MCP container is running.`
  }
  if (err instanceof Error && err.message) {
    // HTTP error — surface the server's detail verbatim
    return err.message
  }
  return "Ingestion failed — unknown error."
}

export function FirstDocumentStep({ state, onChange }: FirstDocumentStepProps) {
  const [phase, setPhase] = useState<Phase>(state.ingested ? "chat" : "choose")
  const [ingestError, setIngestError] = useState<string | null>(null)
  const [ingestProgress, setIngestProgress] = useState<string | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [queryText, setQueryText] = useState("")
  const [queryLoading, setQueryLoading] = useState(false)
  const [response, setResponse] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // React 19 strict mode can double-invoke event handlers during development.
  // Logs showed back-to-back identical POST /upload calls for the same file
  // during beta testing, both returning 400. The ref is cleared in finally so
  // legitimate retries after an error still work.
  const uploadInFlight = useRef(false)

  const handleIngestFile = useCallback(async (file: File) => {
    if (uploadInFlight.current) return
    uploadInFlight.current = true
    setPhase("ingesting")
    setIngestError(null)
    setFileName(file.name)
    setIngestProgress("Parsing document...")

    try {
      setIngestProgress("Chunking & embedding...")
      // Use manual categorization in wizard — AI categorization is unreliable
      // before providers are configured, and adds 2-5s latency
      await uploadFile(file, { categorizeMode: "manual", domain: "general", skipQuality: true, skipMetadata: true })
      setIngestProgress(null)
      // No artificial wait here — the backend's query retry on empty results
      // handles ChromaDB's write-flush window (usually <100ms but can spike on
      // slow disks), and the user's natural think-time before typing the first
      // query dwarfs the flush delay in practice. Deleting the 300ms fixed
      // setTimeout that used to live here: it was too short on slow hardware
      // (silent empty results) and pure latency on fast hardware.
      setPhase("chat")
      onChange({ ...state, ingested: true })
    } catch (err) {
      setIngestError(formatIngestError(err, file.name))
      setPhase("choose")
      setIngestProgress(null)
    } finally {
      uploadInFlight.current = false
    }
  }, [state, onChange])

  const handleSampleContent = useCallback(async () => {
    if (uploadInFlight.current) return
    uploadInFlight.current = true
    setPhase("ingesting")
    setIngestError(null)
    setFileName("sample-knowledge.md")
    setIngestProgress("Ingesting sample content...")

    try {
      // Fetch the bundled sample content and upload it
      const res = await fetch("/sample-knowledge.md")
      if (!res.ok) {
        // Fallback: create a blob with basic content
        const blob = new Blob(
          ["# Cerid AI Overview\n\nCerid AI is a privacy-first personal AI knowledge companion that connects your documents to powerful language models. All data stays on your machine."],
          { type: "text/markdown" },
        )
        const file = new File([blob], "sample-knowledge.md", { type: "text/markdown" })
        await uploadFile(file, { domain: "general", categorizeMode: "manual" })
      } else {
        const text = await res.text()
        const blob = new Blob([text], { type: "text/markdown" })
        const file = new File([blob], "sample-knowledge.md", { type: "text/markdown" })
        await uploadFile(file, { domain: "general", categorizeMode: "manual" })
      }

      setIngestProgress(null)
      setPhase("chat")
      onChange({ ...state, ingested: true })
    } catch (err) {
      setIngestError(formatIngestError(err, "sample-knowledge.md"))
      setPhase("choose")
      setIngestProgress(null)
    } finally {
      uploadInFlight.current = false
    }
  }, [state, onChange])

  const handleQuery = useCallback(async (text: string) => {
    if (!text.trim()) return
    setQueryLoading(true)
    setResponse(null)

    try {
      // Scope query to the just-uploaded document only.
      // Skip reranking and use small top_k for speed — wizard just needs proof-of-life.
      const wizardOpts = { queryScope: "document" as const, scopeRef: fileName ?? undefined, useReranking: false, skipCache: true }
      let result = await queryKB(text.trim(), ["general"], 3, undefined, wizardOpts)
      if (!result.results?.length) {
        for (const delay of [300, 800]) {
          await new Promise((r) => setTimeout(r, delay))
          result = await queryKB(text.trim(), ["general"], 3, undefined, wizardOpts)
          if (result.results?.length) break
        }
      }
      const topResult = result.results?.[0]
      setResponse(
        topResult?.content
          ?? `Found ${result.total_results} result(s) across ${(result.domains_searched ?? []).join(", ") || "all domains"}.`,
      )
      onChange({ ...state, ingested: true, queried: true })
      setPhase("done")
    } catch {
      setResponse("Query failed — the knowledge base may still be indexing. Try again in a moment.")
    } finally {
      setQueryLoading(false)
    }
  }, [state, onChange, fileName])

  const onFilesDropped = useCallback(
    (files: File[]) => {
      const file = files[0]
      if (file) handleIngestFile(file)
    },
    [handleIngestFile],
  )

  const { isDragOver, dragHandlers } = useDragDrop(onFilesDropped)

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <FileText className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-4 text-center text-lg font-semibold">Try It Out</h3>

      {/* Phase: Choose */}
      {phase === "choose" && (
        <div className="space-y-3">
          <p className="text-center text-sm text-muted-foreground">
            Ingest a document and ask your first question to see RAG in action.
          </p>

          {/* Drag-drop upload zone */}
          <div
            {...dragHandlers}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              "relative flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 transition-colors",
              isDragOver
                ? "border-brand bg-brand/5"
                : "border-muted-foreground/20 hover:border-muted-foreground/40",
            )}
          >
            <Upload className="h-6 w-6 text-muted-foreground" />
            <p className="text-xs font-medium">Drop a file or click to upload</p>
            <p className="text-[10px] text-muted-foreground">PDF, TXT, MD, DOCX</p>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXTS}
              className="pointer-events-none hidden"
              tabIndex={-1}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) handleIngestFile(file)
              }}
            />
            {/* Full-zone overlay during drag to prevent OS file handler interception */}
            {isDragOver && (
              <div className="absolute inset-0 z-50" />
            )}
          </div>

          {/* Sample content option */}
          <button
            type="button"
            onClick={handleSampleContent}
            className="flex w-full items-center justify-center gap-2 rounded-lg border bg-card p-3 text-xs font-medium transition-colors hover:bg-brand/5"
          >
            <Sparkles className="h-3.5 w-3.5 text-brand" />
            Use sample content
            <Badge variant="secondary" className="text-[9px]">Quick start</Badge>
          </button>

          {ingestError && (
            <p className="text-center text-xs text-destructive">{ingestError}</p>
          )}
        </div>
      )}

      {/* Phase: Ingesting */}
      {phase === "ingesting" && (
        <div className="flex flex-col items-center gap-3 py-6">
          <Loader2 className="h-6 w-6 animate-spin text-brand" />
          <p className="text-sm font-medium">{fileName}</p>
          <p className="text-xs text-muted-foreground">{ingestProgress}</p>
        </div>
      )}

      {/* Phase: Chat (mini query) */}
      {(phase === "chat" || phase === "done") && (
        <div className="space-y-3">
          {phase === "chat" && (
            <>
              <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-2 text-center text-xs text-green-600 dark:text-green-400">
                <Check className="mr-1 inline h-3 w-3" />
                {fileName} ingested successfully
              </div>

              <p className="text-center text-xs text-muted-foreground">
                Now ask a question about your document:
              </p>

              {/* Suggestion chips */}
              <div className="flex flex-wrap gap-1.5">
                {SUGGESTION_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    onClick={() => handleQuery(chip)}
                    disabled={queryLoading}
                    className="rounded-full border bg-card px-2.5 py-1 text-[10px] text-muted-foreground transition-colors hover:border-brand hover:text-brand"
                  >
                    {chip}
                  </button>
                ))}
              </div>

              {/* Text input */}
              <div className="flex gap-2">
                <input
                  type="text"
                  value={queryText}
                  onChange={(e) => setQueryText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleQuery(queryText) }}
                  placeholder="Or type your own question..."
                  className="flex-1 rounded-lg border bg-card px-3 py-2 text-xs placeholder:text-muted-foreground/50"
                />
                <Button
                  size="sm"
                  onClick={() => handleQuery(queryText)}
                  disabled={!queryText.trim() || queryLoading}
                >
                  {queryLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                </Button>
              </div>
            </>
          )}

          {/* Response */}
          {response && (
            <div className="space-y-2">
              <div className="rounded-lg border bg-card p-3">
                <p className="text-xs leading-relaxed text-foreground">{response}</p>
              </div>
              <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-2 text-center text-xs text-green-600 dark:text-green-400">
                <Check className="mr-1 inline h-3 w-3" />
                Your knowledge base is working!
              </div>
            </div>
          )}

          {queryLoading && (
            <div className="flex items-center justify-center gap-2 py-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span className="text-xs">Querying knowledge base...</span>
            </div>
          )}
        </div>
      )}
    </>
  )
}
