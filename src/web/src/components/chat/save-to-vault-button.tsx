// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SaveToVaultButton — chat-side "Save to vault" action (RAG C3.4).
 *
 * Mounted on assistant message bubbles only. Clicking opens a modal
 * with:
 *   - Vault selector (populated from GET /watched-folders?is_vault=true)
 *   - Path input (default: chat/{conversation-slug}-{message-id-short}.md)
 *   - Mode selector (Create / Append / Overwrite, default Create)
 *   - Content preview (read-only textarea)
 *   - Save button → POST /wiki/write_note → toast on success or failure
 *
 * Errors surface via sonner; the dialog stays open on failure so the
 * user can fix the path / mode and retry without re-opening.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import { Bookmark, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { fetchVaultsList, writeNote, type WriteNoteMode } from "@/lib/api/wiki"

interface SaveToVaultButtonProps {
  /** Assistant message markdown — written verbatim as the note body. */
  content: string
  /** Stable assistant message id (used for the default filename). */
  messageId: string
  /** Optional conversation title — slugified into the default filename. */
  conversationTitle?: string
}

/** RFC-3986-safe slug, 40 chars max. Falls back to "chat" if input is empty. */
function slugify(text: string): string {
  const slug = (text || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40)
  return slug || "chat"
}

/** Short 8-char form of a message ID for filename uniqueness. */
function shortId(id: string): string {
  return id.replace(/[^a-z0-9]/gi, "").slice(0, 8) || "msg"
}

function defaultPath(conversationTitle: string | undefined, messageId: string): string {
  return `chat/${slugify(conversationTitle ?? "chat")}-${shortId(messageId)}.md`
}

export function SaveToVaultButton({
  content,
  messageId,
  conversationTitle,
}: SaveToVaultButtonProps) {
  const [open, setOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              aria-label="Save to vault"
              onClick={() => setOpen(true)}
            >
              <Bookmark className="h-3 w-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Save this response to a vault</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      {/* Inner component only mounts when the dialog opens — keeps the
          useQuery hook out of the message-bubble render tree, which
          would otherwise force every test rendering a MessageBubble to
          wrap in a QueryClientProvider. */}
      {open && (
        <SaveToVaultDialogBody
          content={content}
          messageId={messageId}
          conversationTitle={conversationTitle}
          onClose={() => setOpen(false)}
        />
      )}
    </Dialog>
  )
}

interface SaveToVaultDialogBodyProps {
  content: string
  messageId: string
  conversationTitle?: string
  onClose: () => void
}

function SaveToVaultDialogBody({
  content,
  messageId,
  conversationTitle,
  onClose,
}: SaveToVaultDialogBodyProps) {
  const [vaultId, setVaultId] = useState<string>("")
  const [path, setPath] = useState<string>(() => defaultPath(conversationTitle, messageId))
  const [mode, setMode] = useState<WriteNoteMode>("create")
  const [saving, setSaving] = useState(false)

  const { data: vaults, isLoading: vaultsLoading } = useQuery({
    queryKey: ["save-to-vault", "vaults-list"],
    queryFn: fetchVaultsList,
    staleTime: 30_000,
  })

  // Default the vault selection to the first available vault when the
  // list arrives. Don't overwrite an explicit user choice.
  useEffect(() => {
    if (vaultId || !vaults || vaults.length === 0) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setVaultId(vaults[0].id)
  }, [vaultId, vaults])

  const handleSave = useCallback(async () => {
    if (!vaultId) {
      toast.error("Pick a vault before saving")
      return
    }
    const trimmedPath = path.trim()
    if (!trimmedPath) {
      toast.error("Path is required")
      return
    }

    setSaving(true)
    try {
      const result = await writeNote({
        vault_id: vaultId,
        path: trimmedPath,
        content,
        frontmatter: {
          "cerid:source_message_id": messageId,
        },
        mode,
        allow_synthesis_input: false,
      })
      const ingestNote = result.ingested ? "" : " (file written, re-ingest deferred)"
      toast.success(`Saved to ${result.file_path}${ingestNote}`)
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Save to vault failed"
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }, [vaultId, path, content, messageId, mode, onClose])

  const previewBody = useMemo(() => content, [content])

  return (
    <DialogContent className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle>Save to vault</DialogTitle>
        <DialogDescription>
          Writes this assistant response as a markdown note inside one of
          your registered vaults. The note is re-ingested as a
          ``cerid-synthesis`` artifact, so it stays out of future
          synthesis input sets by default.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="save-to-vault-vault">Vault</Label>
          <Select
            value={vaultId}
            onValueChange={setVaultId}
            disabled={vaultsLoading || saving}
          >
            <SelectTrigger id="save-to-vault-vault" aria-label="Vault">
              <SelectValue placeholder={vaultsLoading ? "Loading vaults…" : "Pick a vault"} />
            </SelectTrigger>
            <SelectContent>
              {(vaults ?? []).map((v) => (
                <SelectItem key={v.id} value={v.id}>
                  {v.label || v.path}
                </SelectItem>
              ))}
              {vaults && vaults.length === 0 && !vaultsLoading && (
                <div className="px-3 py-2 text-sm text-muted-foreground">
                  No vaults registered. Add one in Settings &rarr; Watched
                  folders with ``is_vault=true``.
                </div>
              )}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="save-to-vault-path">Path</Label>
          <Input
            id="save-to-vault-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            disabled={saving}
            placeholder="chat/my-note.md"
          />
          <p className="text-xs text-muted-foreground">
            Relative to the vault root. ``.md`` is appended automatically if you omit it.
          </p>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="save-to-vault-mode">Mode</Label>
          <Select
            value={mode}
            onValueChange={(v) => setMode(v as WriteNoteMode)}
            disabled={saving}
          >
            <SelectTrigger id="save-to-vault-mode" aria-label="Mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="create">Create (fail if exists)</SelectItem>
              <SelectItem value="append">Append to existing</SelectItem>
              <SelectItem value="overwrite">Overwrite existing</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="save-to-vault-preview">Preview</Label>
          <Textarea
            id="save-to-vault-preview"
            value={previewBody}
            readOnly
            rows={6}
            className="font-mono text-xs"
          />
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={handleSave} disabled={saving || !vaultId}>
          {saving ? (
            <>
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> Saving…
            </>
          ) : (
            "Save"
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
