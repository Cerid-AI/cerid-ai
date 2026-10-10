// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { detectAsOf } from "@/lib/as-of"

const TODAY = new Date("2026-10-09T12:00:00Z")

describe("detectAsOf", () => {
  it.each([
    ["What was my salary in 2023?", "2023-12-31"],
    ["Where did I live in June 2021?", "2021-06-30"],
    ["As of March 2024, which plan was I on", "2024-03-31"],
    ["as of 2025-02-14 who was my manager", "2025-02-14"],
    ["What was the rate on Feb 3, 2024", "2024-02-03"],
    ["What did we decide back in 2022", "2022-12-31"],
    ["What was my salary in February 2024?", "2024-02-29"],
  ])("%s → %s", (question, expected) => {
    expect(detectAsOf(question, TODAY)).toBe(expected)
  })

  it.each([
    "What is my salary?",                       // current
    "Summarize the 2023 report",                // a year, but not a historical question
    "What was the last thing I said?",          // past tense, no date
    "What will my salary be in 2030?",          // future
    "What was planned for 2027?",               // a date after today
    "Why did build 2025-03-02 fail?",           // a date that merely appears
    "What did the 2023 audit find?",            // a year used as a name
    "",
  ])("leaves %j to current knowledge", (question) => {
    expect(detectAsOf(question, TODAY)).toBeNull()
  })
})
