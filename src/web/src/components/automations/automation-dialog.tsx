// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import type { Automation, AutomationCreate } from "@/lib/types"
import { cn } from "@/lib/utils"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Loader2 } from "lucide-react"

// ---------------------------------------------------------------------------
// Schedule presets (client-side fallback — server presets take precedence)
// ---------------------------------------------------------------------------

const SCHEDULE_PRESETS: Record<string, { label: string; cron: string }> = {
  daily_9am: { label: "Daily at 9 AM", cron: "0 9 * * *" },
  weekdays_9am: { label: "Weekdays at 9 AM", cron: "0 9 * * 1-5" },
  weekly_monday: { label: "Weekly on Monday", cron: "0 9 * * 1" },
  monthly_1st: { label: "Monthly on the 1st", cron: "0 9 1 * *" },
  custom: { label: "Custom cron...", cron: "" },
}

const ACTION_OPTIONS: { value: AutomationCreate["action"]; label: string; description: string }[] = [
  { value: "notify", label: "Notify", description: "Send a notification with results" },
  { value: "digest", label: "Digest", description: "Generate a knowledge digest" },
  { value: "ingest", label: "Ingest", description: "Auto-ingest new content" },
]

const DOMAIN_OPTIONS = ["coding", "finance", "projects", "personal", "general"]

interface AutomationDialogProps {
  open: boolean
  onClose: () => void
  automation?: Automation
  onSave: (data: AutomationCreate) => void
  saving?: boolean
}

function resolvePresetKey(cron: string): string {
  for (const [key, preset] of Object.entries(SCHEDULE_PRESETS)) {
    if (key !== "custom" && preset.cron === cron) return key
  }
  return "custom"
}

export default function AutomationDialog({ open, onClose, automation, onSave, saving }: AutomationDialogProps) {
  const isEdit = !!automation

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [prompt, setPrompt] = useState("")
  const [scheduleKey, setScheduleKey] = useState("daily_9am")
  const [customCron, setCustomCron] = useState("")
  const [action, setAction] = useState<AutomationCreate["action"]>("notify")
  const [domains, setDomains] = useState<string[]>(["general"])
  const [enabled, setEnabled] = useState(true)

  // Reset form when dialog opens or automation changes
  useEffect(() => {
    if (open) {
      if (automation) {
        setName(automation.name)
        setDescription(automation.description)
        setPrompt(automation.prompt)
        const key = resolvePresetKey(automation.schedule)
        setScheduleKey(key)
        setCustomCron(key === "custom" ? automation.schedule : "")
        setAction(automation.action)
        setDomains(automation.domains.length > 0 ? automation.domains : ["general"])
        setEnabled(automation.enabled)
      } else {
        setName("")
        setDescription("")
        setPrompt("")
        setScheduleKey("daily_9am")
        setCustomCron("")
        setAction("notify")
        setDomains(["general"])
        setEnabled(true)
      }
    }
  }, [open, automation])

  function toggleDomain(domain: string) {
    setDomains((prev) =>
      prev.includes(domain) ? prev.filter((d) => d !== domain) : [...prev, domain],
    )
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const schedule =
      scheduleKey === "custom" ? customCron : SCHEDULE_PRESETS[scheduleKey]?.cron ?? customCron
    if (!name.trim() || !prompt.trim() || !schedule.trim()) return
    onSave({
      name: name.trim(),
      description: description.trim() || undefined,
      prompt: prompt.trim(),
      schedule,
      action,
      domains: domains.length > 0 ? domains : undefined,
      enabled,
    })
  }

  const isValid =
    name.trim().length > 0 &&
    prompt.trim().length > 0 &&
    (scheduleKey !== "custom" || customCron.trim().length > 0)

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Automation" : "New Automation"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-1.5">
            <Label htmlFor="auto-name">Name</Label>
            <Input
              id="auto-name"
              placeholder="e.g. Morning Research Digest"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <Label htmlFor="auto-desc">Description (optional)</Label>
            <textarea
              id="auto-desc"
              rows={2}
              className={cn(
                "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm",
                "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
              placeholder="Brief description of what this automation does"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {/* Prompt */}
          <div className="space-y-1.5">
            <Label htmlFor="auto-prompt">Prompt</Label>
            <textarea
              id="auto-prompt"
              rows={3}
              className={cn(
                "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm",
                "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
              placeholder="What should Cerid do? e.g. Summarize the latest entries in my coding knowledge base"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </div>

          {/* Schedule */}
          <div className="space-y-1.5">
            <Label>Schedule</Label>
            <Select value={scheduleKey} onValueChange={setScheduleKey}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SCHEDULE_PRESETS).map(([key, { label }]) => (
                  <SelectItem key={key} value={key}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {scheduleKey === "custom" && (
              <Input
                placeholder="e.g. 30 8 * * 1-5"
                value={customCron}
                onChange={(e) => setCustomCron(e.target.value)}
                className="mt-1.5 font-mono text-xs"
              />
            )}
          </div>

          {/* Action type */}
          <div className="space-y-1.5">
            <Label>Action Type</Label>
            <div className="flex gap-2">
              {ACTION_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setAction(opt.value)}
                  className={cn(
                    "flex-1 rounded-md border px-3 py-2 text-xs font-medium transition-colors",
                    action === opt.value
                      ? "border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-400"
                      : "border-border bg-transparent text-muted-foreground hover:bg-muted",
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {ACTION_OPTIONS.find((o) => o.value === action)?.description}
            </p>
          </div>

          {/* Domains */}
          <div className="space-y-1.5">
            <Label>Domains</Label>
            <div className="flex flex-wrap gap-2">
              {DOMAIN_OPTIONS.map((domain) => (
                <label
                  key={domain}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                    domains.includes(domain)
                      ? "border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-400"
                      : "border-border text-muted-foreground hover:bg-muted",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={domains.includes(domain)}
                    onChange={() => toggleDomain(domain)}
                    className="sr-only"
                  />
                  {domain}
                </label>
              ))}
            </div>
          </div>

          {/* Enabled */}
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
            <Label htmlFor="auto-enabled" className="cursor-pointer text-sm">
              Enabled
            </Label>
            <Switch id="auto-enabled" checked={enabled} onCheckedChange={setEnabled} />
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" disabled={!isValid || saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isEdit ? "Save Changes" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
