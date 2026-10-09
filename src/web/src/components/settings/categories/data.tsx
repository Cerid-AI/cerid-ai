// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ReceiptText, Trash2 } from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { DataState } from "@/components/ui/data-state"
import { SettingRow, SliderRow, ConfirmActionButton } from "@/components/settings/settings-primitives"
import { ForgetAssistant } from "@/components/settings/forget-assistant"
import { getDef } from "@/lib/settings-registry"
import { QUERY_KEYS } from "@/lib/query-keys"
import {
  emptyTrash,
  fetchReceipt,
  fetchReceipts,
  fetchTrash,
  restoreForget,
  ForgetConflictError,
} from "@/lib/api"
import type { ReceiptSummary, TrashGroup } from "@/lib/api"
import type { SettingsCategoryPageProps } from "./page-props"

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const id = `data-section-${title.toLowerCase().replace(/\s+/g, "-")}`
  return (
    <Card role="region" aria-labelledby={id}>
      <CardHeader className="pb-2">
        <h2 id={id} className="text-label-xs uppercase text-muted-foreground tracking-wider">{title}</h2>
      </CardHeader>
      <CardContent className="density-stack">{children}</CardContent>
    </Card>
  )
}

const formatDate = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function describeSubjects(counts: Record<string, number>): string {
  const parts: string[] = []
  if (counts.conversation) parts.push(plural(counts.conversation, "conversation"))
  const other = (counts.artifact ?? 0) + (counts.memory ?? 0) + (counts.chunk ?? 0)
  if (other) parts.push(plural(other, "document or memory", "documents or memories"))
  return parts.join(", ") || "Nothing"
}

function groupTitle(group: TrashGroup): string {
  const conv = group.subjects.find((s) => s.kind === "conversation")
  if (conv) return conv.label || "Untitled conversation"
  return group.subjects[0]?.label || "Deleted item"
}

function TrashSection() {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const trash = useQuery({ queryKey: QUERY_KEYS.forgetTrash(), queryFn: fetchTrash })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.forgetTrash() })
    void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.forgetReceipts() })
  }

  const restore = async (forgetId: string) => {
    setNotice(null)
    try {
      await restoreForget(forgetId)
      setNotice("Restored. It will reappear in your chats.")
    } catch (err) {
      setNotice(
        err instanceof ForgetConflictError
          ? "This has started erasing and can't be restored."
          : "Couldn't restore. Try again.",
      )
    }
    refresh()
  }

  const items = trash.data ?? []
  return (
    <Section title="Trash">
      <p className="text-sm text-muted-foreground">
        Deleted chats and what you chose to forget with them. Restore puts them back; Empty Trash erases them now
        and keeps a receipt.
      </p>
      {notice && <p role="status" className="text-sm">{notice}</p>}
      <DataState
        loading={trash.isPending}
        error={trash.isError ? trash.error : null}
        onRetry={() => void trash.refetch()}
        empty={!trash.isPending && !trash.isError && items.length === 0}
        emptyIcon={Trash2}
        emptyTitle="Trash is empty"
        compact
      >
        <ul className="divide-y rounded-md border">
          {items.map((group) => {
            const counts: Record<string, number> = {}
            for (const s of group.subjects) counts[s.kind] = (counts[s.kind] ?? 0) + 1
            return (
              <li key={group.forget_id} className="flex flex-wrap items-start justify-between gap-2 p-3">
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium">{groupTitle(group)}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDate(group.at)} · {describeSubjects(counts)}
                  </p>
                  <details className="mt-1 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">What's included</summary>
                    <ul className="mt-1 list-disc pl-4">
                      {group.subjects.map((s) => (
                        <li key={`${s.kind}:${s.id}`}>
                          {s.label || (s.kind === "conversation" ? "Untitled conversation" : "Deleted item")}
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={group.purge_started}
                    aria-label={`Restore ${groupTitle(group)}`}
                    onClick={() => void restore(group.forget_id)}
                  >
                    Restore
                  </Button>
                  {group.purge_started && (
                    <span className="text-xs text-muted-foreground">Erasing — can't be restored</span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </DataState>
      {items.length > 0 && (
        <ConfirmActionButton
          danger="confirm"
          title="Empty the Trash?"
          description="Everything in the Trash is erased now. Each item keeps a receipt."
          actionLabel="Empty Trash"
          variant="destructive"
          size="sm"
          onConfirm={async () => {
            await emptyTrash()
            refresh()
          }}
        >
          Empty Trash
        </ConfirmActionButton>
      )}
    </Section>
  )
}

function ReceiptRow({ receipt }: { receipt: ReceiptSummary }) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: QUERY_KEYS.forgetReceipt(receipt.forget_id),
    queryFn: () => fetchReceipt(receipt.forget_id),
    enabled: open,
  })
  return (
    <li className="p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          {formatDate(receipt.at)} · {describeSubjects(receipt.subjects)} ·{" "}
          <span className={receipt.status === "done" ? "" : "text-amber-600 dark:text-amber-400"}>
            {receipt.status === "done" ? "Erased" : "Still erasing — retried automatically"}
          </span>
        </p>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          aria-label={`Show receipt ${formatDate(receipt.at)}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide" : "Details"}
        </Button>
      </div>
      {open && (
        <DataState
          loading={detail.isPending}
          error={detail.isError ? detail.error : null}
          onRetry={() => void detail.refetch()}
          compact
        >
          {detail.data && (
            <div className="mt-2 space-y-2 text-xs text-muted-foreground">
              <ul>
                {Object.entries(detail.data.adapters).map(([store, a]) => (
                  <li key={store}>
                    {store}: {a.status === "done" ? `removed ${a.removed}` : `pending (${a.error ?? "retrying"})`}
                  </li>
                ))}
              </ul>
              {detail.data.out_of_reach.length > 0 && (
                <div>
                  <p className="font-medium">Not reachable</p>
                  <ul className="list-disc pl-4">
                    {detail.data.out_of_reach.map((note) => <li key={note}>{note}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </DataState>
      )}
    </li>
  )
}

function ReceiptsSection() {
  const receipts = useQuery({ queryKey: QUERY_KEYS.forgetReceipts(), queryFn: fetchReceipts })
  const items = receipts.data ?? []
  return (
    <Section title="Receipts">
      <p className="text-sm text-muted-foreground">
        A record of every erase: which stores removed how much. Receipts never hold the content itself.
      </p>
      <DataState
        loading={receipts.isPending}
        error={receipts.isError ? receipts.error : null}
        onRetry={() => void receipts.refetch()}
        empty={!receipts.isPending && !receipts.isError && items.length === 0}
        emptyIcon={ReceiptText}
        emptyTitle="No receipts yet"
        compact
      >
        <ul className="divide-y rounded-md border">
          {items.map((r) => <ReceiptRow key={r.forget_id} receipt={r} />)}
        </ul>
      </DataState>
    </Section>
  )
}

export default function DataCategory({ settings, patch }: SettingsCategoryPageProps) {
  const def = getDef("data.trash.autoEmptyDays")
  const days = settings.forget_trash_days ?? 30
  return (
    <div className="density-stack">
      <Section title="Keep deleted items">
        {def && (
          <SettingRow def={def}>
            <SliderRow
              label="Days in the Trash"
              value={days}
              onChange={(v) => void patch({ forget_trash_days: Math.round(v) })}
              min={0}
              max={90}
              step={1}
              info={def.helpText}
            />
          </SettingRow>
        )}
        <p className="text-xs text-muted-foreground">
          {days === 0 ? "Never empty automatically" : `Erased automatically after ${plural(days, "day")}`}
        </p>
      </Section>
      <Section title="Forget with the assistant">
        <ForgetAssistant />
      </Section>
      <TrashSection />
      <ReceiptsSection />
    </div>
  )
}
