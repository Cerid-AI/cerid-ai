// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

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
    const day = pairs.day_of_week

    const timeStr = hour !== "*" ? `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}` : null

    if (day && timeStr) return `${capitalize(day)} at ${timeStr}`
    if (day) return `Every ${capitalize(day)}`
    if (timeStr) return `Daily at ${timeStr}`

    return trigger
  }

  if (unwrapped.startsWith("date[")) return "One-time"

  return trigger
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}