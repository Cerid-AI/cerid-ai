// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The date an explicitly historical question asks about, as an ISO date, or
 * null. Retrieval then returns the version of each memory and document that
 * was in force then instead of the current one (forget phase 5, spec §7).
 *
 * Conservative on purpose: a wrong date hides current versions, while a
 * missed one still leaves the history notes on current results to answer
 * "what was it before". Only these count:
 *   - "as of <date>"                           ("as of March 2023", "as of 2023-03-15")
 *   - a past-tense question that names a time   ("what was my salary in 2023",
 *     after in / during / back in / on          "where did I live in June 2021")
 * A date that merely appears ("why did build 2025-03-02 fail") does not count.
 * A year or month means its last day; a date in the future is ignored.
 */

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
]
const MONTH = `(${MONTHS.join("|")}|${MONTHS.map((m) => m.slice(0, 3)).join("|")})\\.?`
const PAST = /\b(was|were|did|had|used to|back then|previously|before)\b/i

// Every date form must follow a preposition that makes it the time asked about.
const LEAD = String.raw`\b(?:as of|in|during|back in|on)\s+`
const ISO_DATE = new RegExp(String.raw`${LEAD}(\d{4})-(\d{2})-(\d{2})\b`, "i")
const MONTH_DAY_YEAR = new RegExp(String.raw`${LEAD}${MONTH}\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b`, "i")
const MONTH_YEAR = new RegExp(String.raw`${LEAD}${MONTH}\s+(\d{4})\b`, "i")
const YEAR = new RegExp(String.raw`${LEAD}((?:19|20)\d{2})\b`, "i")
const AS_OF = /\bas of\b/i

const pad = (n: number) => String(n).padStart(2, "0")

function monthIndex(name: string): number {
  const key = name.toLowerCase().replace(".", "").slice(0, 3)
  return MONTHS.findIndex((m) => m.startsWith(key))
}

function lastDay(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
}

function dateIn(text: string): string | null {
  let m = text.match(ISO_DATE)
  if (m) return `${m[1]}-${m[2]}-${m[3]}`
  m = text.match(MONTH_DAY_YEAR)
  if (m) {
    const month = monthIndex(m[1])
    return `${m[3]}-${pad(month + 1)}-${pad(Number(m[2]))}`
  }
  m = text.match(MONTH_YEAR)
  if (m) {
    const month = monthIndex(m[1])
    const year = Number(m[2])
    return `${year}-${pad(month + 1)}-${pad(lastDay(year, month))}`
  }
  m = text.match(YEAR)
  if (m) return `${m[1]}-12-31`
  return null
}

export function detectAsOf(question: string, today: Date = new Date()): string | null {
  const text = question.trim()
  if (!text) return null
  if (!AS_OF.test(text) && !PAST.test(text)) return null
  const date = dateIn(text)
  if (!date) return null
  const todayIso = today.toISOString().slice(0, 10)
  if (date > todayIso) return null
  return date
}
