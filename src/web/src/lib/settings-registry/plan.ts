// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Public/community edition. Checkout is hosted on cerid.ai — this build never
// talks to a payment provider — but activation is local: a key bought there is
// entered here and validated offline against /license/activate.

import type { SettingDef } from "./types"

const SERVER_SCOPE = {
  scope: "server" as const,
  display: "Applies to this server instance — all sessions.",
}

export const PLAN_DEFS: SettingDef[] = [
  {
    id: "plan.tier.current",
    category: "plan",
    group: "tier",
    level: "core",
    label: "Current plan",
    helpText:
      "Your active plan tier. Community is free and self-hosted with the full core feature set. " +
      "Pro and Enterprise add scheduled automations, Smart RAG customization, and multi-user features.",
    scopeOfEffect: SERVER_SCOPE,
    keywords: ["plan", "tier", "community", "pro", "enterprise", "billing", "upgrade", "Pro"],
    type: "display",
    writer: { kind: "readonly" },
  },
  {
    id: "plan.trial.start",
    category: "plan",
    group: "tier",
    level: "core",
    label: "Free Pro trial",
    helpText:
      "Run the full Pro feature set for 14 days. No credit card, no account — the trial is " +
      "granted locally by this server and can be started once per installation.",
    scopeOfEffect: SERVER_SCOPE,
    keywords: ["trial", "free", "try", "evaluate", "Pro", "14 days"],
    type: "action",
    writer: { kind: "endpoint", method: "POST", path: "/license/trial" },
  },
  {
    id: "plan.license.key",
    category: "plan",
    group: "tier",
    level: "core",
    label: "License key",
    helpText:
      "Paste the key emailed after purchase to unlock Pro on this server. Validation is offline — " +
      "the key never leaves this machine.",
    scopeOfEffect: SERVER_SCOPE,
    keywords: ["license", "license key", "activate", "unlock", "Pro", "key"],
    type: "action",
    writer: { kind: "endpoint", method: "POST", path: "/license/activate" },
  },
  {
    id: "plan.tier.manage",
    category: "plan",
    group: "tier",
    level: "core",
    label: "Manage plan",
    helpText:
      "Plans, checkout, and subscription management live on cerid.ai. A purchased license " +
      "activates Pro or Enterprise features on this server.",
    scopeOfEffect: SERVER_SCOPE,
    keywords: ["upgrade", "manage", "subscription", "license", "pricing", "buy"],
    type: "display",
    writer: { kind: "readonly" },
  },
]
