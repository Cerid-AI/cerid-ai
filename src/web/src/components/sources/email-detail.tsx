// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// EmailDetail — Dialog pane for the Email (IMAP) first-class row.
//
// Extracted from EmailImapSection. Opened when the user selects the "email:imap"
// row in SourcesConnectors. On save/poll/disconnect, invalidates both
// ["email-status"] (so the row status refreshes) and ["ingestion-sources"]
// (so the row count/label stays coherent).

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Mail, Loader2, CheckCircle2, RefreshCw } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  configureEmail,
  fetchEmailStatus,
  pollEmailNow,
  deleteEmailSource,
  type EmailConfig,
} from "@/lib/api/email"
import { notifyError } from "@/lib/query-client"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface EmailDetailProps {
  open: boolean
  onClose: () => void
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

type BusyOp = "saving" | "polling" | "disconnecting" | null

const EMPTY_FORM: EmailConfig = {
  host: "",
  port: 993,
  user: "",
  password: "",
  folder: "INBOX",
  poll_interval: 15,
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export function EmailDetail({ open, onClose }: EmailDetailProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-lg p-0">
        {open && <EmailDetailInner />}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Inner — re-mounts when dialog opens so form is fresh
// ---------------------------------------------------------------------------

function EmailDetailInner() {
  const qc = useQueryClient()
  const [form, setForm] = useState<EmailConfig>(EMPTY_FORM)
  const [busy, setBusy] = useState<BusyOp>(null)
  const [configured, setConfigured] = useState(false)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  const { data: status } = useQuery({
    queryKey: ["email-status"],
    queryFn: fetchEmailStatus,
    staleTime: 30_000,
  })

  const invalidateBoth = () => {
    void qc.invalidateQueries({ queryKey: ["email-status"] })
    void qc.invalidateQueries({ queryKey: ["ingestion-sources"] })
  }

  const update = <K extends keyof EmailConfig>(key: K, value: EmailConfig[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const canSave = form.host.trim() !== "" && form.user.trim() !== "" && form.password !== ""

  const hasActivity =
    configured || !!status?.configured || !!status?.last_poll || (status?.messages_ingested ?? 0) > 0

  const handleSave = async () => {
    setBusy("saving")
    try {
      await configureEmail(form)
      setConfigured(true)
      invalidateBoth()
    } catch (err) {
      notifyError(err, { action: "configureEmail", host: form.host })
    } finally {
      setBusy(null)
    }
  }

  const handlePollNow = async () => {
    setBusy("polling")
    try {
      await pollEmailNow()
      invalidateBoth()
    } catch (err) {
      notifyError(err, { action: "pollEmailNow" })
    } finally {
      setBusy(null)
    }
  }

  const handleConfirmDisconnect = async () => {
    setConfirmDisconnect(false)
    setBusy("disconnecting")
    try {
      await deleteEmailSource()
      setConfigured(false)
      setForm(EMPTY_FORM)
      invalidateBoth()
    } catch (err) {
      notifyError(err, { action: "deleteEmailSource" })
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <div className="space-y-0">
        {/* Header — Liquid Glass */}
        <DialogHeader className="liquid-glass rounded-t-lg px-5 py-4">
          <div className="flex items-center gap-3">
            <Mail className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="flex-1">
              <DialogTitle className="text-base font-medium">Email (IMAP)</DialogTitle>
              <p className="mt-0.5 text-label-xs text-muted-foreground">
                Poll an IMAP mailbox for new mail. Opened read-only — messages are never marked read or deleted.
              </p>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-4 px-5 py-4">
          <Card className="space-y-3 p-3" data-testid="email-imap-card">
            <div className="flex items-center gap-2">
              <Mail className="h-5 w-5 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="font-medium">Mailbox</span>
              {hasActivity && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600">
                  <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> connected
                </span>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="email-host">IMAP host</Label>
                <Input
                  id="email-host"
                  placeholder="imap.fastmail.com"
                  value={form.host}
                  onChange={(e) => update("host", e.target.value)}
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email-port">Port</Label>
                <Input
                  id="email-port"
                  type="number"
                  value={form.port}
                  onChange={(e) => update("port", Number(e.target.value) || 993)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email-user">Username</Label>
                <Input
                  id="email-user"
                  placeholder="you@example.com"
                  value={form.user}
                  onChange={(e) => update("user", e.target.value)}
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email-password">Password</Label>
                <Input
                  id="email-password"
                  type="password"
                  placeholder="app-specific password"
                  value={form.password}
                  onChange={(e) => update("password", e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email-folder">Folder</Label>
                <Input
                  id="email-folder"
                  value={form.folder}
                  onChange={(e) => update("folder", e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email-interval">Poll interval (min)</Label>
                <Input
                  id="email-interval"
                  type="number"
                  value={form.poll_interval}
                  onChange={(e) => update("poll_interval", Number(e.target.value) || 15)}
                />
              </div>
            </div>

            {status?.last_poll && (
              <p className="text-xs text-muted-foreground" data-testid="email-status">
                Last poll: {new Date(status.last_poll).toLocaleString()} · {status.messages_ingested} ingested
                {status.errors.length > 0 && (
                  <span className="text-amber-600"> · {status.errors.length} error{status.errors.length !== 1 && "s"}</span>
                )}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <Button onClick={() => { void handleSave() }} disabled={busy !== null || !canSave} data-testid="email-save">
                {busy === "saving" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Connect / Update"}
              </Button>
              <Button
                variant="outline"
                onClick={() => { void handlePollNow() }}
                disabled={busy !== null || !hasActivity}
                data-testid="email-poll-now"
              >
                {busy === "polling" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <RefreshCw className="mr-1 h-4 w-4" /> Poll now
                  </>
                )}
              </Button>
              {hasActivity && (
                <Button
                  variant="ghost"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setConfirmDisconnect(true)}
                  disabled={busy !== null}
                  data-testid="email-disconnect"
                >
                  {busy === "disconnecting" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Disconnect"}
                </Button>
              )}
            </div>
          </Card>
        </div>
      </div>

      <AlertDialog open={confirmDisconnect} onOpenChange={setConfirmDisconnect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect mailbox?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the stored IMAP credentials and stops polling. Already-ingested mail stays in your
              knowledge base.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => { void handleConfirmDisconnect() }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              data-testid="email-disconnect-confirm"
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
