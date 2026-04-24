// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * One-line plain-English description for every backend feature flag the
 * Settings → System "Platform Capabilities" grid surfaces. Sourced from
 * the canonical ``src/mcp/config/features.py`` plus inline backend
 * comments. Kept colocated here (rather than fetched from the backend)
 * because they're stable strings + frontend-only display copy.
 *
 * The optional ``tier`` field signals when a capability requires a
 * higher tier than community — surfaced as a sub-line in the tooltip.
 */
export interface CapabilityDescriptor {
  description: string
  tier?: "pro" | "vault"
}

export const CAPABILITY_DESCRIPTIONS: Record<string, CapabilityDescriptor> = {
  ocr_parsing: {
    description: "Extract text from scanned PDFs and images. Requires the OCR plugin (pytesseract + tesseract-ocr).",
  },
  audio_transcription: {
    description: "Transcribe audio files into searchable text via Whisper.",
    tier: "pro",
  },
  image_understanding: {
    description: "Caption + describe images during ingest using a vision LLM.",
    tier: "pro",
  },
  semantic_dedup: {
    description: "Detect near-duplicate artifacts at ingest time using embedding similarity, not just exact-hash matching.",
  },
  advanced_analytics: {
    description: "Per-domain quality scoring, freshness decay, and citation-graph analytics in the Analytics tab.",
    tier: "pro",
  },
  metamorphic_verification: {
    description: "Cross-model claim re-verification — runs the same hallucination check against a second LLM and flags disagreements.",
    tier: "pro",
  },
  multi_user: {
    description: "Multi-tenant mode with per-user JWT auth, tenant-scoped retrieval, and admin role gates. Set CERID_MULTI_USER=true in .env.",
    tier: "vault",
  },
  sso_saml: {
    description: "SAML 2.0 single sign-on for enterprise identity providers (Okta, Azure AD, etc.).",
    tier: "vault",
  },
  audit_logging: {
    description: "Tamper-evident audit log of every read/write/admin action, exported to compliance-grade storage.",
    tier: "vault",
  },
  priority_support: {
    description: "Direct email + Slack channel access to the Cerid AI team with same-business-day response.",
    tier: "vault",
  },
  custom_smart_rag: {
    description: "Per-source weight tuning for Smart RAG (KB / memory / external) instead of the default balanced blend.",
    tier: "pro",
  },
  parent_child_retrieval: {
    description: "Parent-document retrieval — returns the surrounding paragraph/section when a chunk matches, for richer context.",
    tier: "pro",
  },
  gmail_connector: {
    description: "Watched-folder ingest from a Gmail mailbox via OAuth — emails become artifacts in real time.",
    tier: "pro",
  },
  outlook_connector: {
    description: "Same as the Gmail connector but for Microsoft 365 / Outlook mailboxes.",
    tier: "pro",
  },
  apple_notes_reader: {
    description: "Read-only ingest of macOS Apple Notes via local SQLite — no network calls.",
    tier: "pro",
  },
  calendar_sync: {
    description: "Sync events from Google / Outlook calendars and link them to mentioned artifacts and people.",
    tier: "pro",
  },
  docling_parser: {
    description: "Use IBM's Docling for high-fidelity PDF + DOCX parsing (tables, layout). Heavier than the default parser.",
    tier: "pro",
  },
  hierarchical_taxonomy: {
    description: "Allow nested sub-categories under each top-level domain (e.g. finance/treasury/invoices).",
  },
  file_upload_gui: {
    description: "Drag-and-drop file upload from the Knowledge tab. When off, ingest is API-only.",
  },
  encryption_at_rest: {
    description: "AES-256 encryption of artifacts and conversation history on disk. Requires CERID_ENCRYPTION_KEY in .env.",
    tier: "vault",
  },
  truth_audit: {
    description: "Periodic re-verification of stored claims against the live web — flags claims that have become stale.",
    tier: "vault",
  },
  live_metrics: {
    description: "Real-time observability metrics (latency p95, cache hit rate, throughput) on the Health tab.",
  },
  private_mode: {
    description: "Block every outbound network call (LLM, web search, telemetry). Useful for offline / air-gapped deployments.",
    tier: "vault",
  },
  basic_workflows: {
    description: "Scheduled automations — recurring digests, RSS ingest, watched-folder polls.",
  },
  advanced_workflows: {
    description: "Multi-step workflow graphs with conditional branching, human-in-the-loop, and webhook triggers.",
    tier: "pro",
  },
}

/** Lookup with a sensible fallback so unknown flags still render. */
export function describeCapability(flag: string): CapabilityDescriptor {
  return (
    CAPABILITY_DESCRIPTIONS[flag] ?? {
      description: "Backend feature flag — see docs/features.md for details.",
    }
  )
}
