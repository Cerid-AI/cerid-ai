// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Glossary — single source of truth for every piece of technical jargon
 * that shows up in the UI. Consumed by `<InfoTip term="…">` wrappers.
 *
 * Rules for adding a term:
 * - `label` is the display text (can differ from the key).
 * - `short` is the 1-line tooltip body — under ~120 chars.
 * - `detail` is optional extended copy shown in a learn-more expansion.
 * - `learnMoreUrl` is optional, only for external docs.
 *
 * When you add a new jargon word anywhere in the UI, add it here first,
 * then wrap the UI occurrence in `<InfoTip term="your-key">label</InfoTip>`.
 */

export interface GlossaryEntry {
  label: string
  short: string
  detail?: string
  learnMoreUrl?: string
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  // ──────────────────────────────────────────────────────────────────────
  // Retrieval / RAG concepts
  // ──────────────────────────────────────────────────────────────────────
  "rag-smart": {
    label: "Smart RAG",
    short:
      "Automatically searches your knowledge base on every message and includes matching context in the AI's reply.",
    detail:
      "Smart mode runs a vector + keyword hybrid search against every domain collection plus any configured external sources, then picks the most relevant passages to inject as system prompt context. Best for most users.",
  },
  "rag-manual": {
    label: "Manual RAG",
    short:
      "Only searches documents you explicitly @mention or drag into the chat. No automatic retrieval.",
  },
  "rag-custom": {
    label: "Custom RAG",
    short:
      "Tune which sources are queried and their relative weights. For power users who want to override the smart ranker.",
  },
  "auto-inject": {
    label: "Auto-Inject KB",
    short:
      "When on, Cerid searches your knowledge base before each message and slips the most relevant passages into the AI's context.",
  },
  "injection-threshold": {
    label: "Injection Threshold",
    short:
      "Minimum similarity score (0–1) a KB chunk must reach before it's injected. Lower = more context, more noise. Typical: 0.15–0.25.",
  },
  "crag-threshold": {
    label: "CRAG Quality Threshold",
    short:
      "If the top KB match's relevance is below this score, Cerid supplements with external web sources. Default: 0.4.",
    detail:
      "CRAG (Corrective RAG) is a fallback that kicks in when your local knowledge base doesn't have a strong enough answer. Raise the threshold to trigger web supplementation more often; lower it to stay local.",
  },

  // ──────────────────────────────────────────────────────────────────────
  // Verification pipeline
  // ──────────────────────────────────────────────────────────────────────
  "response-verification": {
    label: "Response Verification",
    short:
      "Extracts factual claims from the AI's reply and checks each against your KB + optional external sources.",
  },
  "verification-simple": {
    label: "Simple Verification",
    short:
      "Fast, cross-model claim-check — sends each claim to a second LLM to confirm it's supported by evidence.",
  },
  "verification-complex": {
    label: "Complex Verification",
    short:
      "Rigorous, multi-source check — queries Wikipedia / DuckDuckGo / memory + runs NLI entailment before voting.",
  },
  "claim-extraction": {
    label: "Claim Extraction",
    short:
      "Breaks the AI's response into individual factual statements the verifier can evaluate one by one.",
  },
  "authoritative-sources": {
    label: "Authoritative Sources",
    short:
      "External data sources the verifier consults when KB relevance is low — Wikipedia, DuckDuckGo, Wolfram Alpha, etc.",
  },
  "nli-entailment": {
    label: "NLI Entailment",
    short:
      "Natural-language-inference score (0–1) measuring whether one statement logically implies another. Used for verification.",
  },

  // ──────────────────────────────────────────────────────────────────────
  // Observability / metrics
  // ──────────────────────────────────────────────────────────────────────
  "ndcg-at-5": {
    label: "NDCG@5",
    short:
      "Retrieval quality score: how well-ranked are the top 5 results? 0 = random, 1 = perfect. Good systems score > 0.7.",
    detail:
      "Normalised Discounted Cumulative Gain at rank 5. Penalises relevant results that rank low and ignores irrelevant results past position 5. Tracked per query so degradations surface early.",
  },
  "degradation-tier": {
    label: "Degradation Tier",
    short:
      "Current system health grade: Healthy / Degraded / Critical. Based on LLM + KB + embedding availability.",
  },
  "pipeline-routing": {
    label: "Pipeline Routing",
    short:
      "How internal sub-tasks (claim extraction, query decomposition, memory resolution) are routed — local Ollama or OpenRouter.",
  },
  "query-decomposition": {
    label: "Query Decomposition",
    short:
      "Breaks a complex question into sub-questions, retrieves for each, then merges. Useful for multi-part questions.",
  },
  "topic-extraction": {
    label: "Topic Extraction",
    short:
      "Identifies the main topics in a query so the router can pick the best domain collections to search first.",
  },
  "memory-resolution": {
    label: "Memory Resolution",
    short:
      "Searches your saved memories (facts, preferences, past decisions) and includes matches in the response context.",
  },

  // ──────────────────────────────────────────────────────────────────────
  // Modes / flags
  // ──────────────────────────────────────────────────────────────────────
  "private-mode": {
    label: "Private Mode",
    short:
      "Prevents this conversation from being saved as memories, indexed, or sent to any analytics/feedback pipeline.",
  },
  "feedback-loop": {
    label: "Feedback Loop",
    short:
      "When on, AI responses are ingested back into your KB as empirical memories so future answers build on them.",
  },
  "expert-verification": {
    label: "Expert Verification",
    short:
      "Uses a stronger model + authoritative external sources for the verification pass. Higher cost, higher rigor.",
  },
  "smart-routing": {
    label: "Smart Routing",
    short:
      "Automatically picks the cheapest model that's still capable enough for your query's complexity.",
  },

  // ──────────────────────────────────────────────────────────────────────
  // Infrastructure
  // ──────────────────────────────────────────────────────────────────────
  "local-models-status": {
    label: "Local models status",
    short:
      "Number of Ollama models currently loaded and ready for offline inference. Zero = no local fallback.",
  },
  "openrouter-credits": {
    label: "OpenRouter Credits",
    short:
      "Your remaining credit balance at openrouter.ai. Click to top up.",
  },
  "semantic-cache": {
    label: "Semantic Cache",
    short:
      "Caches query results by embedding similarity, not exact text. A paraphrased question hits the same cache entry.",
  },
}

export type GlossaryKey = keyof typeof GLOSSARY
