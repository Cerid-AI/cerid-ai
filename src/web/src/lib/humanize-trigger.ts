// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Convert APScheduler trigger strings into human-readable descriptions.
// Handles interval, cron, and date trigger formats.
export function humanizeTrigger(trigger: string): string {
  if (!trigger) return "Unknown"

  // Strip APScheduler repr wrapper: <CronTrigger (cron[...])> → cron[...]
  const unwrapped = trigger.replace(/^<\w+Trigger\s*\((.+)\)>$/, "$1").trim()

  // interval triggers: interval[hours=1], interval[minutes=30]
  const intervalMatch = unwrapped.match(/^interval\[(\w+)=(\d+)\]$/)
  if (intervalMatch) {
    const [, unit, val] = intervalMatch
    const n = Number(val)
    const singular = unit.replace(/s$/, "")
    return n === 1 ? `Every ${singular}` : `Every ${n} ${unit}`
  }

  // cron triggers: cron[hour='*/6', minute='0']
  const cronMatch = unwrapped.match(/^cron\[(.+)\]$/)
  if (cronMatch) {
    const pairs = Object.fromEntries(
      cronMatch[1].split(",").map((p) => {
        const [k, v] = p.trim().split("=")
        return [k.trim(), v?.replace(/'/g, "").trim() ?? ""]
      }),
    )

    if (pairs.hour?.startsWith("*/")) return `Every ${pairs.hour.slice(2)} hours`
    if (pairs.minute?.startsWith("*/")) return `Every ${pairs.minute.slice(2)} minutes`

    const hour = pairs.hour ?? "*"
    const minute = pairs.minute ?? "0"
    const day = weekdayLabel(pairs.day_of_week)

    const timeStr = hour !== "*" ? `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}` : null

    if (day && timeStr) return `${day} at ${timeStr}`
    if (day) return `Every ${day}`
    if (timeStr) return `Daily at ${timeStr}`

    return trigger
  }

  if (unwrapped.startsWith("date[")) return "One-time"

  return trigger
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// APScheduler's own weekday indexing: 0 = Monday .. 6 = Sunday. This is NOT
// crontab's 0 = Sunday — `CronTrigger.from_crontab("0 5 * * 0")` fires on a
// Monday — so the label has to follow the scheduler, not the crontab source.
const APS_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const

/**
 * Render a cron `day_of_week` field as a weekday label, or null when the
 * field carries no day restriction. Every CronTrigger built from a crontab
 * expression emits a `day_of_week` entry, and its wildcard value means
 * "every day" — printing it raw produced "* at 00:00".
 */
function weekdayLabel(field: string | undefined): string | null {
  if (!field || field === "*") return null
  const parts = field.split(",").map((p) => p.trim()).filter(Boolean)
  if (parts.length === 0) return null
  const named = parts.map((part) => {
    const n = Number(part)
    if (Number.isInteger(n) && n >= 0 && n < APS_WEEKDAYS.length) return APS_WEEKDAYS[n]
    return capitalize(part)
  })
  return named.join(", ")
}