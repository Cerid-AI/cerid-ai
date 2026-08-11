// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// AppleDetail — Dialog pane for Apple bridge sources (notes / mail / imessage).
//
// Opened when the user selects an apple:* row in SourcesConnectors.
// Gate: desktop-only (requires window.cerid.appleConnectors). If somehow
// opened in a browser build, renders a "desktop-only" message instead of
// the bridge UI.
//
// B2: covers ONLY Electron-bridge kinds (notes, mail, imessage).
// The REST connectors (apple_calendar, apple_photos, apple_reminders) appear
// via the A2/A3 connector rows and are NOT duplicated here.

import { useCallback, useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  FileText,
  Mail,
  MessageCircle,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Lock,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { MCP_BASE } from "@/lib/api/common"
import { useEntitlements } from "@/hooks/use-entitlements"
import { ProUpgradeOverlay } from "./pro-upgrade-overlay"

// ---------------------------------------------------------------------------
// Types for the Electron bridge interface — inlined here to keep this module
// self-contained (migrated from apple-connectors-section.tsx which was retired in B2)
// ---------------------------------------------------------------------------

interface NotesScanResult {
  ok: boolean
  total_notes: number
  encrypted_skipped: number
  folder_count: number
  account_count: number
  error?: string
}

interface NotesIngestResult {
  scan: NotesScanResult
  ingest: { ingested: number; failed: number; errors: string[] }
}

interface MailScanResult {
  ok: boolean
  total_messages: number
  account_count: number
  mailbox_count: number
  scanned_with_body: number
  error?: string
}

interface MailIngestResult {
  scan: MailScanResult
  ingest: { ingested: number; failed: number; errors: string[] }
}

interface IMessageConversation {
  chat_id: number
  guid: string
  display_name: string | null
  participants: string[]
  message_count: number
  last_message_at: string | null
  is_group: boolean
}

interface IMessageScanResult {
  ok: boolean
  total_conversations: number
  conversations: IMessageConversation[]
  error?: string
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type AppleBridgeKind = "notes" | "mail" | "imessage"

export interface AppleDetailProps {
  kind: AppleBridgeKind
  open: boolean
  onClose: () => void
}

// ---------------------------------------------------------------------------
// Global type augmentation for the Electron bridge
// (was in apple-connectors-section.tsx before that file was retired in B2)
// ---------------------------------------------------------------------------

interface CeridAppleBridge {
  appleConnectors?: {
    notes: {
      scan: (opts?: { limit?: number }) => Promise<NotesScanResult & { notes: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<NotesIngestResult>
    }
    mail: {
      scan: (opts?: { limit?: number }) => Promise<MailScanResult & { messages: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<MailIngestResult>
    }
    imessage: {
      scan: (opts?: { limit?: number }) => Promise<IMessageScanResult>
      ingest: (payload: {
        mcp_base_url: string
        chat_guids: string[]
        limit_per_chat?: number
      }) => Promise<{
        scan: IMessageScanResult
        ingested: number
        failed: number
        errors: string[]
      }>
    }
    /** Spotlight runs the other way — it reads the KB and donates it to
     *  CoreSpotlight — so it has no scan/ingest pair and no row here. Its
     *  surface is Settings → Extensions. */
    spotlight?: {
      donate: (payload: { mcp_base_url: string; max_items?: number }) => Promise<{
        ok: boolean
        scanned: number
        donated: number
        error?: string
      }>
      purge: () => Promise<{ ok: boolean; error?: string }>
    }
  }
}

declare global {
  interface Window {
    cerid?: CeridAppleBridge
  }
}

// ---------------------------------------------------------------------------
// Desktop availability guard
// ---------------------------------------------------------------------------

function isDesktopAvailable(): boolean {
  return typeof window !== "undefined" && !!window.cerid?.appleConnectors
}

// ---------------------------------------------------------------------------
// Kind metadata
// ---------------------------------------------------------------------------

const KIND_META: Record<AppleBridgeKind, { title: string; blurb: string; Icon: typeof FileText }> = {
  notes: {
    title: "Apple Notes",
    blurb: "Sync from local Notes.app. Requires Full Disk Access.",
    Icon: FileText,
  },
  mail: {
    title: "Apple Mail",
    blurb: "Local Mail.app archive. Requires Full Disk Access.",
    Icon: Mail,
  },
  imessage: {
    title: "iMessage",
    blurb: "Per-conversation opt-in sync. private_mode Level 2+ enforced at retrieval.",
    Icon: MessageCircle,
  },
}

// ---------------------------------------------------------------------------
// Export: Dialog shell (mounts inner lazily so state resets on re-open)
// ---------------------------------------------------------------------------

export function AppleDetail({ kind, open, onClose }: AppleDetailProps) {
  const meta = KIND_META[kind]
  const Icon = meta.Icon
  const [upgradeOpen, setUpgradeOpen] = useState(false)

  // The bridge kinds are Pro, and nothing server-side enforces that: they never
  // reach the plugin loader and ingest through the generic /ingest/structured
  // route. `appleRows(isLocked)` takes a REQUIRED predicate so a new row call
  // site can't ship them unlocked — but the component was reachable without
  // going through a row at all (`<AppleDetail kind="mail" open />` compiled and
  // rendered the scan/ingest pane), which defeated that guarantee. The check
  // belongs at this boundary too, so the promise holds however the pane is
  // mounted.
  //
  // Flags are spelled out one literal at a time, matching SourcesConnectors: a
  // dynamic lookup would be invisible to scripts/lint-pro-gating.py, which is
  // what asserts these renderer gates exist at all. Only "locked" (tier too
  // low) routes to the upgrade path — "flag-off" means the operator disabled a
  // feature the plan already covers, and pitching Pro to a Pro user is wrong.
  const { forFlag, isLoading: entitlementsLoading } = useEntitlements()
  const locks: Record<AppleBridgeKind, boolean> = {
    notes: forFlag("apple_notes_reader", "pro").state === "locked",
    mail: forFlag("apple_mail_reader", "pro").state === "locked",
    imessage: forFlag("imessage_reader", "pro").state === "locked",
  }
  const proLocked = locks[kind]

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-lg p-0">
        <DialogHeader className="liquid-glass rounded-t-lg px-5 py-4">
          <div className="flex items-center gap-3">
            <Icon className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="flex-1">
              <DialogTitle className="text-base font-medium">{meta.title}</DialogTitle>
              <p className="mt-0.5 text-label-xs text-muted-foreground">{meta.blurb}</p>
            </div>
          </div>
        </DialogHeader>

        {open && (
          <div className="px-5 py-4">
            {!isDesktopAvailable() ? (
              // Checked before the Pro gate on purpose: a browser build can
              // never run the bridge whatever the plan, so an upgrade would
              // not fix it. Same rule the gallery applies to requires_desktop
              // tiles — a statement about the build, not about the plan.
              <p className="text-sm text-muted-foreground">
                {meta.title} is a desktop-only source. Open this in the Cerid AI desktop app.
              </p>
            ) : entitlementsLoading ? (
              // Neither verdict is safe yet: `tier` defaults to "community"
              // while capabilities are in flight, so rendering the gate would
              // pitch Pro at a paying customer, and rendering the pane would
              // start the bridge scan before we know the plan allows it.
              // Waiting costs one dialog frame.
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                Checking your plan…
              </p>
            ) : proLocked ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  {meta.title} is part of Cerid Pro.
                </p>
                <Button
                  size="sm"
                  onClick={() => setUpgradeOpen(true)}
                  className="cerid-press"
                >
                  <Lock className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  Unlock with Pro
                </Button>
              </div>
            ) : (
              <AppleDetailInner kind={kind} />
            )}
          </div>
        )}

        <ProUpgradeOverlay
          open={upgradeOpen}
          kind={kind}
          onClose={() => setUpgradeOpen(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Inner — re-mounts when dialog opens so state is fresh
// ---------------------------------------------------------------------------

type BusyOp = "scanning" | "ingesting" | null

function AppleDetailInner({ kind }: { kind: AppleBridgeKind }) {
  const [busy, setBusy] = useState<BusyOp>(null)
  const [error, setError] = useState<string | null>(null)

  // Notes state
  const [notesScan, setNotesScan] = useState<NotesScanResult | null>(null)
  const [notesIngest, setNotesIngest] = useState<NotesIngestResult["ingest"] | null>(null)

  // Mail state
  const [mailScan, setMailScan] = useState<MailScanResult | null>(null)
  const [mailIngest, setMailIngest] = useState<MailIngestResult["ingest"] | null>(null)

  // iMessage state
  const [imessageScan, setImessageScan] = useState<IMessageScanResult | null>(null)
  const [selectedChats, setSelectedChats] = useState<Set<string>>(new Set())
  const [imessageIngest, setImessageIngest] = useState<{
    ingested: number
    failed: number
    errors: string[]
  } | null>(null)

  const scan = useCallback(async () => {
    setBusy("scanning")
    setError(null)
    try {
      const bridge = window.cerid!.appleConnectors!
      if (kind === "notes") {
        const r = await bridge.notes.scan({ limit: 100 })
        const { notes: _n, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
        setNotesScan(rest as NotesScanResult)
      } else if (kind === "mail") {
        const r = await bridge.mail.scan({ limit: 200 })
        const { messages: _m, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
        setMailScan(rest as MailScanResult)
      } else {
        const r = await bridge.imessage.scan({ limit: 100 })
        setImessageScan(r)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed")
    } finally {
      setBusy(null)
    }
  }, [kind])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    void scan()
  }, [scan])

  const ingestNotes = useCallback(async () => {
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.notes.ingest({ mcp_base_url: MCP_BASE })
      setNotesScan({
        ok: r.scan.ok,
        total_notes: r.scan.total_notes,
        encrypted_skipped: r.scan.encrypted_skipped,
        folder_count: r.scan.folder_count,
        account_count: r.scan.account_count,
        error: r.scan.error,
      })
      setNotesIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Notes ingest failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const ingestMail = useCallback(async () => {
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.mail.ingest({ mcp_base_url: MCP_BASE })
      setMailScan({
        ok: r.scan.ok,
        total_messages: r.scan.total_messages,
        account_count: r.scan.account_count,
        mailbox_count: r.scan.mailbox_count,
        scanned_with_body: r.scan.scanned_with_body,
        error: r.scan.error,
      })
      setMailIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mail ingest failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const ingestSelectedChats = useCallback(async () => {
    if (selectedChats.size === 0) return
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.imessage.ingest({
        mcp_base_url: MCP_BASE,
        chat_guids: Array.from(selectedChats),
        limit_per_chat: 5000,
      })
      setImessageIngest({ ingested: r.ingested, failed: r.failed, errors: r.errors })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Messages ingest failed")
    } finally {
      setBusy(null)
    }
  }, [selectedChats])

  const toggleChat = useCallback((guid: string) => {
    setSelectedChats((prev) => {
      const next = new Set(prev)
      if (next.has(guid)) next.delete(guid)
      else next.add(guid)
      return next
    })
  }, [])

  return (
    <div className="space-y-3">
      {error && (
        <div
          className="rounded border border-red-500/30 bg-red-500/5 p-2 text-sm text-red-500"
          role="alert"
        >
          {error}
        </div>
      )}

      {/* Notes kind */}
      {kind === "notes" && (
        <Card
          className={cn(
            "p-3",
            notesScan?.ok && "border-green-500/20",
            notesScan && !notesScan.ok && "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-start gap-3">
            <FileText className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">Apple Notes</span>
                {notesScan?.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                )}
                {notesScan && !notesScan.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                    <AlertTriangle className="h-3 w-3" /> needs access
                  </span>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {notesScan?.ok && (
                <p className="text-xs text-muted-foreground">
                  {notesScan.total_notes} note{notesScan.total_notes !== 1 && "s"}
                  {notesScan.folder_count > 0 && ` · ${notesScan.folder_count} folder${notesScan.folder_count !== 1 ? "s" : ""}`}
                  {notesScan.account_count > 0 && ` · ${notesScan.account_count} account${notesScan.account_count !== 1 ? "s" : ""}`}
                  {notesScan.encrypted_skipped > 0 && (
                    <>
                      {" · "}
                      <span className="text-amber-600">
                        {notesScan.encrypted_skipped} encrypted (skipped)
                      </span>
                    </>
                  )}
                </p>
              )}
              {notesScan && !notesScan.ok && (
                <p className="text-xs text-amber-600">{notesScan.error}</p>
              )}
              {notesIngest && (
                <p className="pt-1 text-xs text-muted-foreground" data-testid="apple-notes-ingest-result">
                  Last sync: {notesIngest.ingested} ingested
                  {notesIngest.failed > 0 && (
                    <span className="text-amber-600"> · {notesIngest.failed} failed</span>
                  )}
                </p>
              )}
            </div>
            {notesScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestNotes() }}
                disabled={busy !== null || notesScan.total_notes === 0}
                data-testid="apple-notes-ingest"
              >
                {busy === "ingesting" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Sync to KB"}
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* Mail kind */}
      {kind === "mail" && (
        <Card
          className={cn(
            "p-3",
            mailScan?.ok && "border-green-500/20",
            mailScan && !mailScan.ok && "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-start gap-3">
            <Mail className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">Apple Mail</span>
                {mailScan?.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                )}
                {mailScan && !mailScan.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                    <AlertTriangle className="h-3 w-3" /> needs access
                  </span>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {mailScan?.ok && (
                <p className="text-xs text-muted-foreground">
                  {mailScan.total_messages} message{mailScan.total_messages !== 1 && "s"}
                  {mailScan.account_count > 0 && ` · ${mailScan.account_count} account${mailScan.account_count !== 1 ? "s" : ""}`}
                  {mailScan.mailbox_count > 0 && ` · ${mailScan.mailbox_count} mailbox${mailScan.mailbox_count !== 1 ? "es" : ""}`}
                  {mailScan.scanned_with_body < mailScan.total_messages && (
                    <span className="text-amber-600">
                      {" · "}
                      {mailScan.total_messages - mailScan.scanned_with_body} body unreadable
                    </span>
                  )}
                </p>
              )}
              {mailScan && !mailScan.ok && (
                <p className="text-xs text-amber-600">{mailScan.error}</p>
              )}
              {mailIngest && (
                <p className="pt-1 text-xs text-muted-foreground" data-testid="apple-mail-ingest-result">
                  Last sync: {mailIngest.ingested} ingested
                  {mailIngest.failed > 0 && (
                    <span className="text-amber-600"> · {mailIngest.failed} failed</span>
                  )}
                </p>
              )}
            </div>
            {mailScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestMail() }}
                disabled={busy !== null || mailScan.total_messages === 0}
                data-testid="apple-mail-ingest"
              >
                {busy === "ingesting" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Sync to KB"}
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* iMessage kind — per-conversation opt-in (privacy-first) */}
      {kind === "imessage" && (
        <Card
          className={cn(
            "p-3",
            imessageScan?.ok && "border-green-500/20",
            imessageScan && !imessageScan.ok && "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-start gap-3">
            <MessageCircle className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex items-center gap-2">
                <span className="font-medium">iMessage</span>
                {imessageScan?.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                )}
                {imessageScan && !imessageScan.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                    <AlertTriangle className="h-3 w-3" /> needs access
                  </span>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {imessageScan?.ok && (
                <>
                  <p className="text-xs text-muted-foreground">
                    {imessageScan.total_conversations} conversation
                    {imessageScan.total_conversations !== 1 && "s"} · select per-conversation to ingest
                  </p>
                  {/* Conversation checklist — label wraps checkbox to avoid nested-interactive */}
                  <ul
                    className="max-h-48 overflow-y-auto space-y-0.5 rounded border p-1"
                    data-testid="imessage-conversation-list"
                  >
                    {imessageScan.conversations.slice(0, 50).map((c) => {
                      const label =
                        c.display_name ?? c.participants.join(", ") ?? `Chat ${c.chat_id}`
                      return (
                        <li key={c.guid}>
                          <label className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-0.5 text-xs hover:bg-accent/40">
                            <input
                              type="checkbox"
                              checked={selectedChats.has(c.guid)}
                              onChange={() => toggleChat(c.guid)}
                              data-testid={`imessage-chat-${c.guid}`}
                            />
                            <span className="min-w-0 flex-1 truncate" title={label}>
                              {label}
                            </span>
                            <span className="shrink-0 text-muted-foreground">
                              {c.message_count} msg
                              {c.is_group && " · group"}
                            </span>
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                </>
              )}
              {imessageScan && !imessageScan.ok && (
                <p className="text-xs text-amber-600">{imessageScan.error}</p>
              )}
              {imessageIngest && (
                <p className="text-xs text-muted-foreground" data-testid="imessage-ingest-result">
                  Last sync: {imessageIngest.ingested} conversation
                  {imessageIngest.ingested !== 1 && "s"} ingested
                  {imessageIngest.failed > 0 && (
                    <span className="text-amber-600"> · {imessageIngest.failed} failed</span>
                  )}
                </p>
              )}
            </div>
            {imessageScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestSelectedChats() }}
                disabled={busy !== null || selectedChats.size === 0}
                data-testid="imessage-ingest"
              >
                {busy === "ingesting" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  `Sync ${selectedChats.size}`
                )}
              </Button>
            )}
          </div>
        </Card>
      )}

      <p className="text-xs italic text-muted-foreground">
        Encrypted notes are never decrypted — only counted. Messages ingest is opt-in
        per conversation; private_mode Level 2+ is enforced at retrieval.
      </p>
    </div>
  )
}
