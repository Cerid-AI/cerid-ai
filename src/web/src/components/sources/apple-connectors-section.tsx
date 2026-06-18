// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Apple connectors section (Phase D Day 3+).
//
// Displayed inside the Sources → Connectors panel for users running the
// Cerid AI desktop app. Lists the macOS-side data sources that flow
// through the Electron host process (not the MCP server's plugin layer),
// because they need direct macOS framework access + TCC grants.
//
// v1 ships: Apple Notes. Mail + Messages land in Phase D Days 5-8.

import { useCallback, useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  FileText,
  Mail,
  MessageCircle,
  ListChecks,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { MCP_BASE } from "@/lib/api/common"

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

interface RemindersScanResult {
  ok: boolean
  total_reminders: number
  list_count: number
  error?: string
}

interface RemindersIngestResult {
  scan: RemindersScanResult
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
    reminders: {
      scan: (opts?: {
        since?: string
        limit?: number
      }) => Promise<RemindersScanResult & { reminders: unknown[] }>
      ingest: (payload: {
        mcp_base_url: string
        since?: string
        limit?: number
      }) => Promise<RemindersIngestResult>
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
  }
}

declare global {
  interface Window {
    cerid?: CeridAppleBridge
  }
}

type BusyOp =
  | "scanning"
  | "ingesting-notes"
  | "ingesting-mail"
  | "ingesting-reminders"
  | "ingesting-imessage"
  | null

export function AppleConnectorsSection() {
  const [notesScan, setNotesScan] = useState<NotesScanResult | null>(null)
  const [notesIngest, setNotesIngest] = useState<NotesIngestResult["ingest"] | null>(null)
  const [mailScan, setMailScan] = useState<MailScanResult | null>(null)
  const [mailIngest, setMailIngest] = useState<MailIngestResult["ingest"] | null>(null)
  const [remindersScan, setRemindersScan] = useState<RemindersScanResult | null>(null)
  const [remindersIngest, setRemindersIngest] = useState<RemindersIngestResult["ingest"] | null>(null)
  const [imessageScan, setImessageScan] = useState<IMessageScanResult | null>(null)
  const [selectedChats, setSelectedChats] = useState<Set<string>>(new Set())
  const [imessageIngest, setImessageIngest] = useState<{
    ingested: number
    failed: number
    errors: string[]
  } | null>(null)
  const [busy, setBusy] = useState<BusyOp>(null)
  const [error, setError] = useState<string | null>(null)

  const desktopAvailable =
    typeof window !== "undefined" && !!window.cerid?.appleConnectors

  const refreshAll = useCallback(async () => {
    if (!desktopAvailable) return
    setBusy("scanning")
    setError(null)
    try {
      const bridge = window.cerid!.appleConnectors!
      // We strip the heavy `notes`/`messages` payload arrays from the
      // scan responses — the summary view only needs counts.
      const [notes, mail, reminders, imessage] = await Promise.all([
        bridge.notes.scan({ limit: 100 }).then((r) => {
          const { notes: _notes, ...rest } = r  // eslint-disable-line @typescript-eslint/no-unused-vars
          return rest as NotesScanResult
        }),
        bridge.mail.scan({ limit: 200 }).then((r) => {
          const { messages: _messages, ...rest } = r  // eslint-disable-line @typescript-eslint/no-unused-vars
          return rest as MailScanResult
        }),
        bridge.reminders.scan({ limit: 500 }).then((r) => {
          const { reminders: _reminders, ...rest } = r  // eslint-disable-line @typescript-eslint/no-unused-vars
          return rest as RemindersScanResult
        }),
        bridge.imessage.scan({ limit: 100 }),
      ])
      setNotesScan(notes)
      setMailScan(mail)
      setRemindersScan(reminders)
      setImessageScan(imessage)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed")
    } finally {
      setBusy(null)
    }
  }, [desktopAvailable])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    refreshAll()
  }, [refreshAll])

  const ingestNotes = useCallback(async () => {
    if (!desktopAvailable) return
    setBusy("ingesting-notes")
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
  }, [desktopAvailable])

  const ingestMail = useCallback(async () => {
    if (!desktopAvailable) return
    setBusy("ingesting-mail")
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
  }, [desktopAvailable])

  const ingestReminders = useCallback(async () => {
    if (!desktopAvailable) return
    setBusy("ingesting-reminders")
    try {
      const r = await window.cerid!.appleConnectors!.reminders.ingest({ mcp_base_url: MCP_BASE })
      setRemindersScan({
        ok: r.scan.ok,
        total_reminders: r.scan.total_reminders,
        list_count: r.scan.list_count,
        error: r.scan.error,
      })
      setRemindersIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reminders ingest failed")
    } finally {
      setBusy(null)
    }
  }, [desktopAvailable])

  const ingestSelectedChats = useCallback(async () => {
    if (!desktopAvailable || selectedChats.size === 0) return
    setBusy("ingesting-imessage")
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
  }, [desktopAvailable, selectedChats])

  const toggleChat = useCallback((guid: string) => {
    setSelectedChats((prev) => {
      const next = new Set(prev)
      if (next.has(guid)) next.delete(guid)
      else next.add(guid)
      return next
    })
  }, [])

  if (!desktopAvailable) {
    return null
  }

  return (
    <div className="space-y-3" data-testid="apple-connectors-section">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Apple Sources</h3>
          <p className="text-xs text-muted-foreground">
            macOS-native data sources. Requires Full Disk Access.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={refreshAll}
          disabled={busy !== null}
          aria-label="Refresh Apple connectors scan"
          data-testid="apple-connectors-refresh"
        >
          <RefreshCw className={cn("w-4 h-4", busy === "scanning" && "animate-spin")} />
        </Button>
      </div>

      {error && (
        <div
          className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5"
          role="alert"
        >
          {error}
        </div>
      )}

      {/* Notes */}
      <Card
        className={cn(
          "p-3",
          notesScan?.ok && "border-green-500/20",
          notesScan && !notesScan.ok && "border-amber-500/30 bg-amber-500/5",
        )}
        data-testid="apple-notes-row"
      >
        <div className="flex items-start gap-3">
          <FileText className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">Apple Notes</span>
              {notesScan?.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600">
                  <CheckCircle2 className="w-3 h-3" /> ready
                </span>
              )}
              {notesScan && !notesScan.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                  <AlertTriangle className="w-3 h-3" /> needs access
                </span>
              )}
            </div>
            {notesScan?.ok && (
              <p className="text-xs text-muted-foreground">
                {notesScan.total_notes} note{notesScan.total_notes !== 1 && "s"}
                {notesScan.folder_count > 0 && ` · ${notesScan.folder_count} folders`}
                {notesScan.account_count > 0 && ` · ${notesScan.account_count} accounts`}
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
            {notesScan && !notesScan.ok && <p className="text-xs text-amber-600">{notesScan.error}</p>}
            {notesIngest && (
              <p className="text-xs text-muted-foreground pt-1" data-testid="apple-notes-ingest-result">
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
              onClick={ingestNotes}
              disabled={busy !== null || notesScan.total_notes === 0}
              data-testid="apple-notes-ingest"
            >
              {busy === "ingesting-notes" ? <Loader2 className="w-4 h-4 animate-spin" /> : "Sync to KB"}
            </Button>
          )}
        </div>
      </Card>

      {/* Mail */}
      <Card
        className={cn(
          "p-3",
          mailScan?.ok && "border-green-500/20",
          mailScan && !mailScan.ok && "border-amber-500/30 bg-amber-500/5",
        )}
        data-testid="apple-mail-row"
      >
        <div className="flex items-start gap-3">
          <Mail className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">Apple Mail</span>
              {mailScan?.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600">
                  <CheckCircle2 className="w-3 h-3" /> ready
                </span>
              )}
              {mailScan && !mailScan.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                  <AlertTriangle className="w-3 h-3" /> needs access
                </span>
              )}
            </div>
            {mailScan?.ok && (
              <p className="text-xs text-muted-foreground">
                {mailScan.total_messages} message{mailScan.total_messages !== 1 && "s"}
                {mailScan.account_count > 0 && ` · ${mailScan.account_count} accounts`}
                {mailScan.mailbox_count > 0 && ` · ${mailScan.mailbox_count} mailboxes`}
                {mailScan.scanned_with_body < mailScan.total_messages && (
                  <span className="text-amber-600">
                    {" · "}
                    {mailScan.total_messages - mailScan.scanned_with_body} body unreadable
                  </span>
                )}
              </p>
            )}
            {mailScan && !mailScan.ok && <p className="text-xs text-amber-600">{mailScan.error}</p>}
            {mailIngest && (
              <p className="text-xs text-muted-foreground pt-1" data-testid="apple-mail-ingest-result">
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
              onClick={ingestMail}
              disabled={busy !== null || mailScan.total_messages === 0}
              data-testid="apple-mail-ingest"
            >
              {busy === "ingesting-mail" ? <Loader2 className="w-4 h-4 animate-spin" /> : "Sync to KB"}
            </Button>
          )}
        </div>
      </Card>

      {/* Reminders */}
      <Card
        className={cn(
          "p-3",
          remindersScan?.ok && "border-green-500/20",
          remindersScan && !remindersScan.ok && "border-amber-500/30 bg-amber-500/5",
        )}
        data-testid="apple-reminders-row"
      >
        <div className="flex items-start gap-3">
          <ListChecks className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">Apple Reminders</span>
              {remindersScan?.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600">
                  <CheckCircle2 className="w-3 h-3" /> ready
                </span>
              )}
              {remindersScan && !remindersScan.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                  <AlertTriangle className="w-3 h-3" /> needs access
                </span>
              )}
            </div>
            {remindersScan?.ok && (
              <p className="text-xs text-muted-foreground">
                {remindersScan.total_reminders} reminder{remindersScan.total_reminders !== 1 && "s"}
                {remindersScan.list_count > 0 && ` · ${remindersScan.list_count} lists`}
              </p>
            )}
            {remindersScan && !remindersScan.ok && (
              <p className="text-xs text-amber-600">{remindersScan.error}</p>
            )}
            {remindersIngest && (
              <p
                className="text-xs text-muted-foreground pt-1"
                data-testid="apple-reminders-ingest-result"
              >
                Last sync: {remindersIngest.ingested} ingested
                {remindersIngest.failed > 0 && (
                  <span className="text-amber-600"> · {remindersIngest.failed} failed</span>
                )}
              </p>
            )}
          </div>
          {remindersScan?.ok && (
            <Button
              variant="outline"
              size="sm"
              onClick={ingestReminders}
              disabled={busy !== null || remindersScan.total_reminders === 0}
              data-testid="apple-reminders-ingest"
            >
              {busy === "ingesting-reminders" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                "Sync to KB"
              )}
            </Button>
          )}
        </div>
      </Card>

      {/* iMessage — per-conversation opt-in (privacy-first) */}
      <Card
        className={cn(
          "p-3",
          imessageScan?.ok && "border-green-500/20",
          imessageScan && !imessageScan.ok && "border-amber-500/30 bg-amber-500/5",
        )}
        data-testid="imessage-row"
      >
        <div className="flex items-start gap-3">
          <MessageCircle className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0 space-y-2">
            <div className="flex items-center gap-2">
              <span className="font-medium">iMessage</span>
              {imessageScan?.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600">
                  <CheckCircle2 className="w-3 h-3" /> ready
                </span>
              )}
              {imessageScan && !imessageScan.ok && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                  <AlertTriangle className="w-3 h-3" /> needs access
                </span>
              )}
            </div>
            {imessageScan?.ok && (
              <>
                <p className="text-xs text-muted-foreground">
                  {imessageScan.total_conversations} conversation
                  {imessageScan.total_conversations !== 1 && "s"} · select per-conversation to ingest
                </p>
                <ul
                  className="max-h-32 overflow-y-auto space-y-0.5 border rounded p-1"
                  data-testid="imessage-conversation-list"
                >
                  {imessageScan.conversations.slice(0, 50).map((c) => {
                    const label =
                      c.display_name ?? c.participants.join(", ") ?? `Chat ${c.chat_id}`
                    return (
                      <li key={c.guid} className="flex items-center gap-2 text-xs px-1.5 py-0.5">
                        <input
                          type="checkbox"
                          checked={selectedChats.has(c.guid)}
                          onChange={() => toggleChat(c.guid)}
                          data-testid={`imessage-chat-${c.guid}`}
                        />
                        <span className="flex-1 truncate" title={label}>
                          {label}
                        </span>
                        <span className="text-muted-foreground">
                          {c.message_count} msg
                          {c.is_group && " · group"}
                        </span>
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
              onClick={ingestSelectedChats}
              disabled={busy !== null || selectedChats.size === 0}
              data-testid="imessage-ingest"
            >
              {busy === "ingesting-imessage" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                `Sync ${selectedChats.size}`
              )}
            </Button>
          )}
        </div>
      </Card>

      <p className="text-xs text-muted-foreground italic">
        Encrypted notes are never decrypted — only counted. Messages ingest is opt-in
        per conversation; private_mode Level 2+ is enforced at retrieval.
      </p>
    </div>
  )
}
