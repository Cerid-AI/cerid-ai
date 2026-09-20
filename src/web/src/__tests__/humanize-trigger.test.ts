// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { humanizeTrigger } from "@/lib/humanize-trigger"

// F369 — APScheduler emits `day_of_week` for every CronTrigger built from a
// crontab expression, including the wildcard. Rendering the raw field value
// produced "* at 00:00" for a daily job and "0 at 05:00" for a weekly one.
//
// Numeric day_of_week follows APScheduler's own weekday indexing (0 = Monday),
// verified against apscheduler 3.11.2:
//   CronTrigger.from_crontab("0 5 * * 0").get_next_fire_time(...) → a Monday.
describe("humanizeTrigger — cron day_of_week", () => {
  it("treats a wildcard day_of_week as 'every day'", () => {
    expect(
      humanizeTrigger("cron[month='*', day='*', day_of_week='*', hour='0', minute='0']"),
    ).toBe("Daily at 00:00")
  })

  it("treats a wildcard day_of_week as 'every day' at a non-midnight hour", () => {
    expect(
      humanizeTrigger("cron[month='*', day='*', day_of_week='*', hour='3', minute='0']"),
    ).toBe("Daily at 03:00")
  })

  it("maps a numeric day_of_week to the weekday APScheduler actually fires on", () => {
    expect(
      humanizeTrigger("cron[month='*', day='*', day_of_week='0', hour='5', minute='0']"),
    ).toBe("Mon at 05:00")
  })

  it("maps every numeric weekday index", () => {
    const expected = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    expected.forEach((name, i) => {
      expect(humanizeTrigger(`cron[day_of_week='${i}', hour='6', minute='30']`)).toBe(
        `${name} at 06:30`,
      )
    })
  })

  it("maps a numeric day_of_week with no hour to a weekly description", () => {
    expect(humanizeTrigger("cron[day_of_week='6', minute='0']")).toBe("Every Sun")
  })

  it("still renders named weekdays", () => {
    expect(humanizeTrigger("cron[day_of_week='sun', hour='2', minute='0']")).toBe("Sun at 02:00")
  })

  it("keeps unrelated trigger forms working", () => {
    expect(humanizeTrigger("interval[hours=1]")).toBe("Every hour")
    expect(humanizeTrigger("<IntervalTrigger (interval[minutes=30])>")).toBe("Every 30 minutes")
    expect(humanizeTrigger("cron[hour='*/6', minute='0']")).toBe("Every 6 hours")
    expect(humanizeTrigger("date[2026-09-03 05:00:00]")).toBe("One-time")
    expect(humanizeTrigger("")).toBe("Unknown")
  })
})
