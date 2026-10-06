// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import {
  PRIVATE_MODE_EGRESS_NOTE,
  PRIVATE_MODE_LEVELS,
} from "@/lib/private-mode-levels"
import type { SettingDef } from "./types"

export const PRIVACY_DEFS: SettingDef[] = [
  // ── Private Mode L0–L4 ─────────────────────────────────────────────────────
  {
    id: "privacy.mode.level",
    category: "privacy",
    group: "mode",
    level: "core",
    label: "Private Mode",
    helpText:
      "Controls how much of a chat persists and what the model is given. " +
      PRIVATE_MODE_LEVELS.map((l) => `L${l.level} = ${l.name}.`).join(" ") +
      " " + PRIVATE_MODE_EGRESS_NOTE,
    scopeOfEffect: {
      scope: "server",
      display: "Global for this server — all tabs and sessions.",
    },
    keywords: [
      "private", "privacy", "mode", "level", "logging", "KB", "ephemeral", "session",
      "wipe", "no logging", "Essentials",
    ],
    type: "enum",
    options: PRIVATE_MODE_LEVELS.map((l) => ({ value: l.level, label: l.label, helpText: l.description })),
    default: 0,
    writer: { kind: "endpoint", method: "POST", path: "/settings/private-mode" },
    mirrors: ["chat-toolbar"],
    writtenBy: "Chat toolbar",
  },
  // ── Encryption ─────────────────────────────────────────────────────────────
  {
    id: "privacy.data.encryption",
    category: "privacy",
    group: "data",
    level: "core",
    label: "Encryption at rest",
    helpText: "Whether Cerid encrypts stored KB data. Controlled by CERID_ENCRYPTION in .env — restart required.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set CERID_ENCRYPTION in .env and restart.",
    },
    keywords: ["encryption", "encrypt", "at rest", "security", "CERID_ENCRYPTION", "System"],
    type: "display",
    writer: { kind: "env", envVar: "CERID_ENCRYPTION" },
  },
  // ── Anonymization / Audit (read-only env rows) ──────────────────────────────
  {
    id: "privacy.data.anonymizeEmailHeaders",
    category: "privacy",
    group: "data",
    level: "advanced",
    label: "Anonymize email headers",
    helpText: "Strip personally-identifying email headers during ingestion. Controlled by CERID_ANONYMIZE_EMAIL_HEADERS.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set CERID_ANONYMIZE_EMAIL_HEADERS in .env and restart.",
    },
    keywords: ["anonymize", "email", "headers", "PII", "CERID_ANONYMIZE_EMAIL_HEADERS", "Governance"],
    type: "display",
    writer: { kind: "env", envVar: "CERID_ANONYMIZE_EMAIL_HEADERS" },
  },
  {
    id: "privacy.data.auditRetentionDays",
    category: "privacy",
    group: "data",
    level: "advanced",
    label: "Audit log retention (days)",
    helpText: "How many days audit log entries are retained. Controlled by AUDIT_RETENTION_DAYS.",
    scopeOfEffect: {
      scope: "env",
      display: "Read-only here — set AUDIT_RETENTION_DAYS in .env and restart.",
    },
    keywords: ["audit", "retention", "log", "days", "AUDIT_RETENTION_DAYS", "Governance"],
    type: "display",
    writer: { kind: "env", envVar: "AUDIT_RETENTION_DAYS" },
  },
]
