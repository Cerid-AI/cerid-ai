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
// Covers the Electron-bridge kinds. Calendar and Photos joined them on
// 2026-08-11: their REST plugins run in the MCP server, which is a Linux
// container that cannot execute a macOS helper, so they could never configure.
// They are now excluded from /connectors so each renders exactly one surface.

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
  CalendarDays,
  Images,
  ListTodo,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Lock,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { MCP_BASE } from "@/lib/api/common"
import { useEntitlements } from "@/hooks/use-entitlements"
import { EntitlementsUnavailableNote } from "@/components/shared/entitlements-error-notice"
import { useNavigation } from "@/contexts/navigation-context"
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
  /** `skipped` counts body-less messages the ingest loop passed over, so
   *  ingested + failed + skipped equals the number of messages handed in —
   *  the same shortfall the scan line reports as "body unreadable". */
  ingest: { ingested: number; failed: number; skipped: number; errors: string[] }
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

interface CalendarScanResult {
  ok: boolean
  total_events: number
  calendar_count: number
  /** True when TCC refused the helper. An empty calendar and a denied one both
   *  yield zero events; only this tells them apart. */
  denied?: boolean
  error?: string
}

interface PhotosScanResult {
  ok: boolean
  total_photos: number
  /** True when the user granted a "Selected Photos" (limited-library) TCC
   *  scope — the scan sees only that selection, which otherwise reads as a
   *  small library. */
  limited?: boolean
  denied?: boolean
  error?: string
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

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type AppleBridgeKind = "notes" | "mail" | "imessage" | "calendar" | "photos" | "reminders"

export interface AppleDetailProps {
  kind: AppleBridgeKind
  open: boolean
  onClose: () => void
}

// ---------------------------------------------------------------------------
// Global type augmentation for the Electron bridge
// (was in apple-connectors-section.tsx before that file was retired in B2)
// ---------------------------------------------------------------------------

interface BackgroundSyncProgress {
  kind: string
  state: "scanning" | "syncing" | "paused" | "done" | "error"
  total: number
  posted: number
  failed: number
  skippedFromCursor: number
  ratePerMin: number | null
  etaSeconds: number | null
  startedAt: string
  lastError: string | null
}

interface CeridAppleBridge {
  /** Slice of the desktop bridge's `app` namespace the web UI consumes.
      Full surface in packages/desktop/src/preload/preload.ts. */
  app?: {
    platform: () => Promise<string>
  }
  appleConnectors?: {
    notes: {
      scan: (opts?: { limit?: number }) => Promise<NotesScanResult & { notes: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<NotesIngestResult>
    }
    mail: {
      scan: (opts?: { limit?: number }) => Promise<MailScanResult & { messages: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<MailIngestResult>
    }
    /** Resumable background sync (sf-1) — runs in the main process,
     *  survives window close, resumes from a persisted cursor after
     *  quit/relaunch. Absent on older desktop builds; callers fall back
     *  to the awaited `ingest` path above. */
    sync?: {
      start: (payload: {
        kind: "mail" | "notes"
        mcp_base_url: string
        limit?: number
      }) => Promise<{ started: boolean; reason?: string }>
      status: () => Promise<BackgroundSyncProgress[]>
      pause: (kind?: "mail" | "notes") => Promise<{ success: boolean }>
      resume: (kind?: "mail" | "notes") => Promise<{ success: boolean }>
      onProgress: (cb: (progress: BackgroundSyncProgress) => void) => () => void
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
        /** Messages dropped because they had no text and their attributedBody
         *  did not decode. */
        skipped_no_text: number
        /** Per-conversation shortfall reports ("<chat>: ingested N of M messages"). */
        notes: string[]
        errors: string[]
      }>
    }
    calendar: {
      scan: (opts?: { limit?: number }) => Promise<CalendarScanResult & { events: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<{
        scan: CalendarScanResult
        ingest: { ingested: number; failed: number; errors: string[] }
      }>
    }
    photos: {
      scan: (opts?: { limit?: number }) => Promise<PhotosScanResult & { photos: unknown[] }>
      ingest: (payload: { mcp_base_url: string; limit?: number }) => Promise<{
        scan: PhotosScanResult
        ingest: { ingested: number; failed: number; errors: string[] }
      }>
    }
    reminders: {
      scan: (opts?: { since?: string; limit?: number }) => Promise<RemindersScanResult & { reminders: unknown[] }>
      ingest: (payload: { mcp_base_url: string; since?: string; limit?: number }) => Promise<RemindersIngestResult>
    }
    /** Spotlight runs the other way — it reads the KB and donates it to
     *  CoreSpotlight — so it has no scan/ingest pair and no row here. Its
     *  surface is Settings → Extensions. */
    spotlight?: {
      donate: (payload: {
        mcp_base_url: string
        max_items?: number
        /** Retention window in days; 0 means never expire. Omit for the
         *  main process's 90-day default. */
        expiration_days?: number
      }) => Promise<{
        ok: boolean
        scanned: number
        donated: number
        /** Items CoreSpotlight rejected — non-zero means part of the donation
         *  did not land. */
        failed?: number
        /** Items dropped before indexing because the record would not decode. */
        skipped?: number
        /** True when the knowledge-base read hit its cap: `scanned` is the
         *  cap, not the KB's size. */
        truncated?: boolean
        /** Window the main process actually applied — it normalises the
         *  request, so this is not necessarily what was asked for. */
        expiration_days?: number
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
// Scan/ingest limits
//
// One constant per kind, used by BOTH the scan call and the ingest call.
// Scan-vs-ingest limit drift silently rewrote the totals the user had
// already read (scan said "100 notes", Sync then re-scanned 5000), and a
// capped scan rendered as a census. When a scan returns exactly the limit,
// the summary copy presents it as a preview, not a total.
// ---------------------------------------------------------------------------

const SCAN_LIMITS: Record<AppleBridgeKind, number> = {
  notes: 100,
  mail: 200,
  imessage: 100,
  calendar: 500,
  photos: 1000,
  reminders: 200,
}

/** iMessage renders at most this many conversations in the checklist. */
const IMESSAGE_LIST_CAP = 50

/** "first {limit} notes (scan preview)" when the cap was hit, else "5 notes". */
function countCopy(total: number, limit: number, unit: string, pluralUnit?: string) {
  const plural = pluralUnit ?? `${unit}s`
  if (total >= limit) return `first ${limit} ${plural} (scan preview)`
  return `${total} ${total === 1 ? unit : plural}`
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
  calendar: {
    title: "Apple Calendar",
    blurb: "Events from EventKit, read on this Mac. Requires the Calendars permission.",
    Icon: CalendarDays,
  },
  photos: {
    title: "Apple Photos",
    blurb: "Photo metadata only — never pixel data. Requires the Photos permission.",
    Icon: Images,
  },
  reminders: {
    title: "Apple Reminders",
    blurb: "Reminders read locally via the EventKit helper. Requires the Reminders permission.",
    Icon: ListTodo,
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
  const { forFlag, isLoading: entitlementsLoading, isError: entitlementsError } = useEntitlements()
  const locks: Record<AppleBridgeKind, boolean> = {
    notes: forFlag("apple_notes_reader", "pro").state === "locked",
    mail: forFlag("apple_mail_reader", "pro").state === "locked",
    imessage: forFlag("imessage_reader", "pro").state === "locked",
    calendar: forFlag("apple_calendar_eventkit", "pro").state === "locked",
    photos: forFlag("apple_photos_reader", "pro").state === "locked",
    reminders: forFlag("reminders_eventkit", "pro").state === "locked",
  }
  const proLocked = locks[kind]

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl p-0">
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
            ) : proLocked && entitlementsError ? (
              <EntitlementsUnavailableNote />
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
              <AppleDetailInner kind={kind} onClose={onClose} />
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

/** Engine-side connector id for a bridge kind (matches X-Client-ID and the
 *  shared backend sync state). iMessage stays per-conversation opt-in and
 *  has no background bulk sync. */
const ENGINE_KIND: Partial<Record<AppleBridgeKind, string>> = {
  mail: "apple_mail",
  notes: "apple_notes",
}

function formatEta(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

// `onClose` is threaded in so cross-pane navigation (Fix permission, View in
// Activity) can close the dialog before switching panes — a navigate that
// leaves the dialog open would render the destination underneath it.
function AppleDetailInner({ kind, onClose }: { kind: AppleBridgeKind; onClose: () => void }) {
  const [busy, setBusy] = useState<BusyOp>(null)
  const [error, setError] = useState<string | null>(null)
  const { goTo } = useNavigation()

  // Every "needs access" / "needs permission" badge routes here: the grant UI
  // (PermissionsStep) has a permanent home in Settings → System, and an amber
  // badge with no action was a dead end.
  const fixPermission = useCallback(() => {
    onClose()
    goTo("settings", { category: "system", setting: "system.permissions" })
  }, [onClose, goTo])

  // After a successful ingest, close the loop: the live ingestion stream is
  // the "verify it worked" surface, two tabs away in Sources → Activity.
  const viewActivity = useCallback(() => {
    onClose()
    goTo("sources", { sourcesMode: "activity" })
  }, [onClose, goTo])

  // Live background-sync progress for this kind (sf-1). Hydrated from
  // sync.status() on mount — a sync started earlier (or resumed after a
  // relaunch) may already be running — then streamed per item.
  const [syncProgress, setSyncProgress] = useState<BackgroundSyncProgress | null>(null)
  const engineKind = ENGINE_KIND[kind]
  useEffect(() => {
    const sync = window.cerid?.appleConnectors?.sync
    if (!sync || !engineKind) return
    void sync.status().then((list) => {
      const current = list.find((s) => s.kind === engineKind)
      if (current) setSyncProgress(current)
    })
    return sync.onProgress((p) => {
      if (p.kind === engineKind) setSyncProgress(p)
    })
  }, [engineKind])

  const syncActive =
    syncProgress != null && ["scanning", "syncing", "paused"].includes(syncProgress.state)

  // Notes state
  const [notesScan, setNotesScan] = useState<NotesScanResult | null>(null)
  const [notesIngest, setNotesIngest] = useState<NotesIngestResult["ingest"] | null>(null)

  // Mail state
  const [mailScan, setMailScan] = useState<MailScanResult | null>(null)
  const [mailIngest, setMailIngest] = useState<MailIngestResult["ingest"] | null>(null)

  // Calendar / Photos state — one shape each, no per-item selection.
  const [calendarScan, setCalendarScan] = useState<CalendarScanResult | null>(null)
  const [photosScan, setPhotosScan] = useState<PhotosScanResult | null>(null)
  type IngestCounts = { ingested: number; failed: number; errors: string[] }
  const [calendarIngest, setCalendarIngest] = useState<IngestCounts | null>(null)
  const [photosIngest, setPhotosIngest] = useState<IngestCounts | null>(null)

  // Reminders state
  const [remindersScan, setRemindersScan] = useState<RemindersScanResult | null>(null)
  const [remindersIngest, setRemindersIngest] = useState<RemindersIngestResult["ingest"] | null>(null)

  // iMessage state
  const [imessageScan, setImessageScan] = useState<IMessageScanResult | null>(null)
  const [selectedChats, setSelectedChats] = useState<Set<string>>(new Set())
  const [imessageIngest, setImessageIngest] = useState<{
    ingested: number
    failed: number
    skipped_no_text: number
    notes: string[]
    errors: string[]
  } | null>(null)

  const scan = useCallback(async () => {
    setBusy("scanning")
    setError(null)
    try {
      const bridge = window.cerid!.appleConnectors!
      // A switch, not an if/else chain ending in a catch-all. The old final
      // `else` meant iMessage, so ANY kind added later would silently scan
      // iMessage instead — a wrong answer rather than an error.
      switch (kind) {
        case "notes": {
          const r = await bridge.notes.scan({ limit: SCAN_LIMITS.notes })
          const { notes: _n, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
          setNotesScan(rest as NotesScanResult)
          break
        }
        case "mail": {
          const r = await bridge.mail.scan({ limit: SCAN_LIMITS.mail })
          const { messages: _m, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
          setMailScan(rest as MailScanResult)
          break
        }
        case "imessage": {
          setImessageScan(await bridge.imessage.scan({ limit: SCAN_LIMITS.imessage }))
          break
        }
        case "calendar": {
          const r = await bridge.calendar.scan({ limit: SCAN_LIMITS.calendar })
          const { events: _e, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
          setCalendarScan(rest as CalendarScanResult)
          break
        }
        case "photos": {
          const r = await bridge.photos.scan({ limit: SCAN_LIMITS.photos })
          const { photos: _p, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
          setPhotosScan(rest as PhotosScanResult)
          break
        }
        case "reminders": {
          const r = await bridge.reminders.scan({ limit: SCAN_LIMITS.reminders })
          const { reminders: _rem, ...rest } = r // eslint-disable-line @typescript-eslint/no-unused-vars
          setRemindersScan(rest as RemindersScanResult)
          break
        }
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

  const ingestCalendar = useCallback(async () => {
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.calendar.ingest({
        mcp_base_url: MCP_BASE,
        limit: SCAN_LIMITS.calendar,
      })
      setCalendarScan(r.scan)
      setCalendarIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingest failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const ingestPhotos = useCallback(async () => {
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.photos.ingest({
        mcp_base_url: MCP_BASE,
        limit: SCAN_LIMITS.photos,
      })
      setPhotosScan(r.scan)
      setPhotosIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingest failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const ingestReminders = useCallback(async () => {
    setBusy("ingesting")
    try {
      const r = await window.cerid!.appleConnectors!.reminders.ingest({
        mcp_base_url: MCP_BASE,
        limit: SCAN_LIMITS.reminders,
      })
      setRemindersScan(r.scan)
      setRemindersIngest(r.ingest)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reminders ingest failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const ingestNotes = useCallback(async () => {
    // Prefer the resumable background sync: returns immediately, streams
    // per-item progress, and survives window close / app relaunch.
    const sync = window.cerid?.appleConnectors?.sync
    if (sync) {
      setError(null)
      const res = await sync.start({ kind: "notes", mcp_base_url: MCP_BASE })
      if (!res.started && res.reason !== "already_running") {
        setError(res.reason ?? "background sync failed to start")
      }
      return
    }
    setBusy("ingesting")
    try {
      // Same limit as the scan: omitting it fell through to the main-process
      // default (5000), so Sync ingested a different population than the one
      // the scan showed and the user consented to.
      const r = await window.cerid!.appleConnectors!.notes.ingest({
        mcp_base_url: MCP_BASE,
        limit: SCAN_LIMITS.notes,
      })
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
    const sync = window.cerid?.appleConnectors?.sync
    if (sync) {
      setError(null)
      const res = await sync.start({ kind: "mail", mcp_base_url: MCP_BASE })
      if (!res.started && res.reason !== "already_running") {
        setError(res.reason ?? "background sync failed to start")
      }
      return
    }
    setBusy("ingesting")
    try {
      // Same limit as the scan — see the note in ingestNotes (mail's
      // main-process default is 500 vs the 200 shown by the scan).
      const r = await window.cerid!.appleConnectors!.mail.ingest({
        mcp_base_url: MCP_BASE,
        limit: SCAN_LIMITS.mail,
      })
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
      setImessageIngest({
        ingested: r.ingested,
        failed: r.failed,
        skipped_no_text: r.skipped_no_text,
        notes: r.notes,
        errors: r.errors,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Messages ingest failed")
    } finally {
      setBusy(null)
    }
  }, [selectedChats])

  // Small shared affordances for the per-kind cards below.
  const fixPermissionButton = (
    <Button
      variant="outline"
      size="sm"
      className="h-6 px-2 text-xs"
      onClick={fixPermission}
      data-testid="fix-permission"
    >
      Fix permission
    </Button>
  )
  const viewInActivityLink = (
    <button
      type="button"
      onClick={viewActivity}
      className="ml-1 text-primary underline underline-offset-2 hover:opacity-80"
      data-testid="view-in-activity"
    >
      View in Activity
    </button>
  )

  // Shared live-progress line for the mail/notes cards. N/M, rate, ETA and
  // failures from the background queue — the same numbers the tray and the
  // backend sync state carry, so the card can no longer contradict itself.
  const renderSyncProgress = () => {
    if (!syncProgress) return null
    if (syncProgress.state === "done") {
      return (
        <p className="pt-1 text-xs text-muted-foreground" data-testid={`apple-${kind}-sync-done`}>
          Last sync: {syncProgress.posted} ingested
          {syncProgress.skippedFromCursor > 0 && ` (${syncProgress.skippedFromCursor} resumed)`}
          {syncProgress.failed > 0 && (
            <span className="text-amber-600"> · {syncProgress.failed} failed</span>
          )}
        </p>
      )
    }
    if (syncProgress.state === "error") {
      return (
        <p className="pt-1 text-xs text-amber-600" data-testid={`apple-${kind}-sync-error`}>
          Sync error: {syncProgress.lastError ?? "unknown"}
        </p>
      )
    }
    const done = syncProgress.posted + syncProgress.skippedFromCursor
    return (
      <div className="space-y-1 pt-1" data-testid={`apple-${kind}-sync-progress`}>
        <p className="text-xs tabular-nums text-foreground">
          {syncProgress.state === "scanning"
            ? "Scanning…"
            : `${done}/${syncProgress.total || "?"} synced`}
          {syncProgress.ratePerMin != null && ` · ${syncProgress.ratePerMin}/min`}
          {syncProgress.etaSeconds != null && ` · ~${formatEta(syncProgress.etaSeconds)} left`}
          {syncProgress.failed > 0 && (
            <span className="text-amber-600"> · {syncProgress.failed} failed</span>
          )}
          {syncProgress.state === "paused" && " · paused"}
        </p>
        {syncProgress.total > 0 && (
          <div
            className="h-1 overflow-hidden rounded-full bg-muted/30"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={syncProgress.total}
            aria-valuenow={done}
          >
            <div
              className="h-full rounded-full bg-brand transition-[width]"
              style={{ width: `${Math.min(100, (done / syncProgress.total) * 100)}%` }} // drift-allowed: progress-bar width is runtime data, not a design token
            />
          </div>
        )}
        <p className="text-label-xs text-muted-foreground">
          Sync runs in the background — closing this window won&apos;t stop it.
        </p>
      </div>
    )
  }

  const syncingChip = (
    <span className="inline-flex items-center gap-1 text-xs text-brand">
      <Loader2 className="h-3 w-3 animate-spin" /> syncing
    </span>
  )

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
                {notesScan?.ok && (syncActive ? (
                  syncingChip
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                ))}
                {notesScan && !notesScan.ok && (
                  <>
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <AlertTriangle className="h-3 w-3" /> needs access
                    </span>
                    {fixPermissionButton}
                  </>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {notesScan?.ok && (
                <p className="text-xs text-muted-foreground">
                  {countCopy(notesScan.total_notes, SCAN_LIMITS.notes, "note")}
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
                  {notesIngest.ingested > 0 && viewInActivityLink}
                </p>
              )}
              {renderSyncProgress()}
            </div>
            {notesScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestNotes() }}
                disabled={busy !== null || syncActive || notesScan.total_notes === 0}
                data-testid="apple-notes-ingest"
              >
                {busy === "ingesting" || syncActive ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Sync to KB"
                )}
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
                {mailScan?.ok && (syncActive ? (
                  syncingChip
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                ))}
                {mailScan && !mailScan.ok && (
                  <>
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <AlertTriangle className="h-3 w-3" /> needs access
                    </span>
                    {fixPermissionButton}
                  </>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {mailScan?.ok && (
                <p className="text-xs text-muted-foreground">
                  {countCopy(mailScan.total_messages, SCAN_LIMITS.mail, "message")}
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
                  {mailIngest.skipped > 0 && (
                    <span className="text-amber-600"> · {mailIngest.skipped} skipped (body unreadable)</span>
                  )}
                  {mailIngest.ingested > 0 && viewInActivityLink}
                </p>
              )}
              {renderSyncProgress()}
            </div>
            {mailScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestMail() }}
                disabled={busy !== null || syncActive || mailScan.total_messages === 0}
                data-testid="apple-mail-ingest"
              >
                {busy === "ingesting" || syncActive ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Sync to KB"
                )}
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* Reminders kind */}
      {kind === "reminders" && (
        <Card
          className={cn(
            "p-3",
            remindersScan?.ok && "border-green-500/20",
            remindersScan && !remindersScan.ok && "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-start gap-3">
            <ListTodo className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">Apple Reminders</span>
                {remindersScan?.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                )}
                {remindersScan && !remindersScan.ok && (
                  <>
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <AlertTriangle className="h-3 w-3" /> needs access
                    </span>
                    {fixPermissionButton}
                  </>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {remindersScan?.ok && (
                <p className="text-xs text-muted-foreground">
                  {countCopy(remindersScan.total_reminders, SCAN_LIMITS.reminders, "reminder")}
                  {remindersScan.list_count > 0 && ` · ${remindersScan.list_count} list${remindersScan.list_count !== 1 ? "s" : ""}`}
                </p>
              )}
              {remindersScan && !remindersScan.ok && (
                <p className="text-xs text-amber-600">{remindersScan.error}</p>
              )}
              {remindersIngest && (
                <p className="pt-1 text-xs text-muted-foreground" data-testid="apple-reminders-ingest-result">
                  Last sync: {remindersIngest.ingested} ingested
                  {remindersIngest.failed > 0 && (
                    <span className="text-amber-600"> · {remindersIngest.failed} failed</span>
                  )}
                  {remindersIngest.ingested > 0 && viewInActivityLink}
                </p>
              )}
            </div>
            {remindersScan?.ok && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void ingestReminders() }}
                disabled={busy !== null || remindersScan.total_reminders === 0}
                data-testid="apple-reminders-ingest"
              >
                {busy === "ingesting" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Sync to KB"}
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* iMessage kind — per-conversation opt-in (privacy-first) */}
      {(kind === "calendar" || kind === "photos") && (
        <Card
          className={cn(
            "p-3",
            (kind === "calendar" ? calendarScan : photosScan)?.ok && "border-green-500/20",
            (kind === "calendar" ? calendarScan : photosScan) &&
              !(kind === "calendar" ? calendarScan : photosScan)?.ok &&
              "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-start gap-3">
            {kind === "calendar" ? (
              <CalendarDays className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            ) : (
              <Images className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{KIND_META[kind].title}</span>
                {(kind === "calendar" ? calendarScan : photosScan)?.ok && (
                  <span className="inline-flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle2 className="h-3 w-3" /> ready
                  </span>
                )}
                {/* `denied` is why the exit code is threaded all the way here:
                    a refused helper and an empty library both return zero
                    items, and only one of them is the user's to fix. */}
                {(kind === "calendar" ? calendarScan : photosScan)?.denied && (
                  <>
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <AlertTriangle className="h-3 w-3" /> needs permission
                    </span>
                    {fixPermissionButton}
                  </>
                )}
                {busy === "scanning" && (
                  <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                )}
              </div>

              {kind === "calendar" && calendarScan?.ok && (
                <p className="text-xs text-muted-foreground" data-testid="calendar-summary">
                  {countCopy(calendarScan.total_events, SCAN_LIMITS.calendar, "event")}
                  {calendarScan.calendar_count > 0 &&
                    ` · ${calendarScan.calendar_count} calendar${calendarScan.calendar_count !== 1 ? "s" : ""}`}
                </p>
              )}
              {kind === "photos" && photosScan?.ok && (
                <p className="text-xs text-muted-foreground" data-testid="photos-summary">
                  {countCopy(photosScan.total_photos, SCAN_LIMITS.photos, "item")} · metadata
                  only, no images are read
                </p>
              )}
              {/* A limited grant looks exactly like a small library — say which
                  it is, here on the pane and not only in the one-time wizard. */}
              {kind === "photos" && photosScan?.ok && photosScan.limited && (
                <p className="text-xs text-amber-600" data-testid="photos-limited">
                  Limited access: only the photos you selected in System
                  Settings are visible. {" "}
                  <button
                    type="button"
                    onClick={fixPermission}
                    className="underline underline-offset-2 hover:opacity-80"
                  >
                    Expand access
                  </button>
                </p>
              )}

              {(kind === "calendar" ? calendarScan : photosScan)?.error && (
                <p className="text-xs text-amber-600">
                  {(kind === "calendar" ? calendarScan : photosScan)?.error}
                </p>
              )}

              {(kind === "calendar" ? calendarIngest : photosIngest) && (
                <div className="space-y-0.5">
                  <p className="text-xs text-muted-foreground" data-testid="ingest-summary">
                    Ingested {(kind === "calendar" ? calendarIngest : photosIngest)?.ingested}
                    {((kind === "calendar" ? calendarIngest : photosIngest)?.failed ?? 0) > 0 &&
                      ` · ${(kind === "calendar" ? calendarIngest : photosIngest)?.failed} failed`}
                    {((kind === "calendar" ? calendarIngest : photosIngest)?.ingested ?? 0) > 0 &&
                      viewInActivityLink}
                  </p>
                  {/* The per-item errors were collected all along and never
                      shown — "N failed" with no reason is not actionable. */}
                  {((kind === "calendar" ? calendarIngest : photosIngest)?.errors ?? []).map(
                    (e) => (
                      <p key={e} className="text-xs text-amber-600" data-testid="ingest-error">
                        {e}
                      </p>
                    ),
                  )}
                </div>
              )}

              <div className="flex gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={() => void scan()} disabled={busy !== null}>
                  Rescan
                </Button>
                <Button
                  size="sm"
                  onClick={() => void (kind === "calendar" ? ingestCalendar() : ingestPhotos())}
                  disabled={busy !== null || !(kind === "calendar" ? calendarScan : photosScan)?.ok}
                  data-testid="apple-ingest"
                >
                  {busy === "ingesting" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ingest"}
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

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
                  <>
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <AlertTriangle className="h-3 w-3" /> needs access
                    </span>
                    {fixPermissionButton}
                  </>
                )}
                {busy === "scanning" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              </div>
              {imessageScan?.ok && (
                <>
                  <p className="text-xs text-muted-foreground">
                    {countCopy(imessageScan.total_conversations, SCAN_LIMITS.imessage, "conversation")}
                    {" · select per-conversation to ingest"}
                    {imessageScan.conversations.length > IMESSAGE_LIST_CAP &&
                      ` · showing ${IMESSAGE_LIST_CAP} of ${imessageScan.conversations.length}`}
                  </p>
                  {/* Conversation checklist — label wraps checkbox to avoid nested-interactive */}
                  <ul
                    className="max-h-48 overflow-y-auto space-y-0.5 rounded border p-1"
                    data-testid="imessage-conversation-list"
                  >
                    {imessageScan.conversations.slice(0, IMESSAGE_LIST_CAP).map((c) => {
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
                    {imessageScan.conversations.length > IMESSAGE_LIST_CAP && (
                      <li
                        className="px-1.5 py-1 text-center text-xs text-muted-foreground"
                        data-testid="imessage-list-end"
                      >
                        End of list — {imessageScan.conversations.length - IMESSAGE_LIST_CAP} more
                        scanned conversation
                        {imessageScan.conversations.length - IMESSAGE_LIST_CAP !== 1 && "s"} not shown
                      </li>
                    )}
                  </ul>
                </>
              )}
              {imessageScan && !imessageScan.ok && (
                <p className="text-xs text-amber-600">{imessageScan.error}</p>
              )}
              {imessageIngest && (
                <div className="space-y-0.5" data-testid="imessage-ingest-result">
                  <p className="text-xs text-muted-foreground">
                    Last sync: {imessageIngest.ingested} conversation
                    {imessageIngest.ingested !== 1 && "s"} ingested
                    {imessageIngest.failed > 0 && (
                      <span className="text-amber-600"> · {imessageIngest.failed} failed</span>
                    )}
                    {imessageIngest.skipped_no_text > 0 && (
                      <span className="text-amber-600">
                        {" · "}
                        {imessageIngest.skipped_no_text} message
                        {imessageIngest.skipped_no_text !== 1 && "s"} skipped (no readable text)
                      </span>
                    )}
                    {imessageIngest.ingested > 0 && viewInActivityLink}
                  </p>
                  {imessageIngest.notes.map((n) => (
                    <p key={n} className="text-xs text-muted-foreground" data-testid="imessage-ingest-note">
                      {n}
                    </p>
                  ))}
                </div>
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
        {KIND_DISCLAIMER[kind]}
      </p>
    </div>
  )
}

// UX-25 — one privacy footer per kind. The shared footer used to show the
// Notes/iMessage disclaimer under every dialog, which was wrong (and
// misleading) on Mail and the other kinds.
const KIND_DISCLAIMER: Record<AppleBridgeKind, string> = {
  notes:
    "Encrypted notes are never decrypted — only counted. All processing stays on this Mac.",
  mail:
    "Mail is read from the local Mail.app archive on this Mac — nothing is sent to your mail server. private_mode Level 2+ is enforced at retrieval.",
  imessage:
    "Messages ingest is opt-in per conversation; private_mode Level 2+ is enforced at retrieval.",
  calendar:
    "Events are read on this Mac via EventKit — never fetched from iCloud servers.",
  photos:
    "Photo metadata only — pixel data never leaves Photos.",
  reminders:
    "Reminders are read locally via the EventKit helper. All processing stays on this Mac.",
}
