# Cerid AI — Product Story

> **Last reviewed:** 2026-05-15 (v0.95.1 release — primitive descriptions still reflect shipped behaviour; cerid-kb MCP overhaul did not change the five primitives, only the surface area).
> **Canonical narrative.** Drift gate: `scripts/lint-product-story.py`
> asserts this file exists, has a `## Last reviewed:` line within 90 days
> of the most recent release tag, and references the five primitives.
> New features that don't serve this story require explicit
> `# Anti-scope rationale` in the relevant `tasks/<date>-*.md` driver.

## What Cerid is

Cerid is the only personal knowledge companion that **accumulates verified
understanding** as you feed it — with every contradiction in your corpus,
every entity Cerid has learned about, every concept it synthesizes —
visible to you on a page you can read.

Self-hosted. Privacy-first. Your data stores stay local; what leaves the
machine is only what you send to your chosen LLM provider — chat/query
context, and (by default) per-document snippets for categorization and
claims for verification. Every egress path is listed in the Data Egress
panel. Run fully local to send nothing.

## The five primitives

Every feature in Cerid serves one of these. If a proposed feature doesn't,
it goes in the anti-scope list of the driver `tasks/` document and waits
for a release that does need it.

### 1. Verification — per-claim, with provenance

Every claim Cerid produces is verified at the **datapoint level**, not the
response level. The canonical `<VerifiedResponse>` component renders each
claim with one of three bands — `verified` / `partial` / `unverified` —
backed by NLI entailment against the source corpus. Confidence numbers,
provenance artifacts, and source chunks are one hover away. The
`ClaimVerification` Pydantic model is the contract; the import-linter
keeps it in `core/`.

Verification answers exactly one question: *"is this claim accurate?"*
Other quality signals (faithfulness, retrieval, feedback) live on their
own surfaces and do not bundle into the per-claim badge.

### 2. TrustScore — system evaluation posture

A single chip in the app header summarizes Cerid's overall evaluation
posture as a number 0–100 with five disclosed components:

- **Faithfulness** (nightly RAGAS, target ≥ 0.90)
- **Retrieval quality** (nightly IR NDCG@10 vs baseline)
- **Memory recall** (weekly LongMemEval run, target ≥ 0.80)
- **Verification coverage** (rolling 24 h, ≥ 95 %)
- **Preservation health** (last `main` CI, all gates passing)

A sixth — **User agreement** — joins once the feedback loop ships.

The score is the straight mean of the normalized components. No learned
weights. No proprietary formula. Honesty over cleverness. Hover discloses
the components; click opens a modal with sparklines and explainers
cross-linking to `docs/EVAL_BASELINES.md` and `docs/PRESERVATION.md`.

The TrustScore is **not a verification gate** — it does not affect
retrieval, generation, or any model decision. It is pure presentation,
designed to make Cerid's eval rigor legible.

### 3. Narrative Loop — the system talks back

Most "second brain" systems are designed for input and never push value
back. Cerid does. The Narrative Loop closes that gap:

- **Daily brief** (06:00) — three sections: Connections (cross-references
  the user probably missed), Pattern (one implicit theme), Question (one
  prompt worth sitting with). Generated against the last 24 h of inbox
  and 7 days of notes.
- **Weekly synthesis** (Monday 06:00) — four sections: Emerging thesis,
  Contradictions (drawn from the W.4 ledger), Knowledge gaps, One action.

Both surfaces are read in the **Briefs pane**, where every claim is run
through claim extraction + KB verification at generation time and rendered
through `<VerifiedResponse>` with its `verified` / `partial` / `unverified`
band. The brief is not opinion — it is verified synthesis.

### 4. Wiki — accumulated understanding made visible

GraphRAG, community detection, NLI verification, and memory consolidation
already build a structured understanding inside Cerid. The Wiki phase
makes that understanding **visible to the user as readable pages**.

- **Entity pages** auto-generate from the corpus and refresh in the
  background when evidence shifts. Each page: summary, related entities,
  source citations, contradictions in this corpus about this entity,
  external references (if Wikipedia / GitHub / etc. enrichment is on),
  last-updated timestamp.
- **Contradiction ledger** persists every disagreement the NLI guard
  detects, dated and sourced. Surfaces in entity pages, weekly synthesis,
  and a standalone `/wiki/contradictions` route.
- **Pre-computed snapshots** ship with curated knowledge packs. A user
  installing a pack sees populated wiki pages within seconds, not after
  hours of local processing.

The Wiki is what makes Cerid a *thinking partner* rather than a *queryable
storage system*.

### 5. Background Processor — continuous, throttled, cost-aware

Every async unit of work in Cerid — parsing, entity extraction,
community refresh, wiki page summarization, memory consolidation, brief
generation, eval runs — flows through one queue. One worker. One throttle.
One pause button. One cost projection.

Three modes:
- **Local-only** (default) — all jobs against local Ollama. Zero API spend.
- **Hybrid** — local for cheap jobs, API for expensive ones. User-configurable
  cost cap with auto-fallback to local when breached.
- **Disabled** — queue accumulates; nothing executes until re-enabled.

CPU-aware: worker dequeue pauses when load average exceeds the configured
ceiling. The system keeps up with what's reasonable for the host; users
see queue depth and can pay for API speedup if they choose.

Every job is visible in the Monitoring pane. Every cost is projected
before submission and tracked after. Every wiki page being refreshed
shows its updating state in real time.

## Why the primitives reinforce each other

- **Verification** makes the wiki trustworthy.
- **GraphRAG entities** are the wiki pages.
- **Community detection** organizes the wiki.
- **Memory consolidation** is the time-series under each entity.
- **Narrative Loop** tells you what changed in the wiki overnight.
- **TrustScore** shows the wiki's evaluation posture.
- **Contradiction log** is the historical record of how understanding shifted.
- **Background Processor** is the engine that keeps all of the above current.

That is one product, not five features.

## What Cerid is not

- A queryable RAG-as-a-service. (It is a companion you live with.)
- A visual agent builder. (That's Dify's strength; Cerid's strength is
  verification depth.)
- A hosted SaaS. (Self-host is the product. Privacy is the moat.)
- A marketing site with a chat widget. (The widget exists, but it's a
  surface, not the product.)

## How features earn their place

Every feature in Cerid must answer:

1. **Which primitive does it serve?** (If none, it's anti-scope.)
2. **What preservation gate enforces it?** (If none, it's not durable.)
3. **What does it look like in a screenshot of the product?** (If
   invisible, it belongs in operator docs, not in the product narrative.)
4. **What does removing it break for the user?** (If nothing, it's
   removable.)

Features that answer all four crisply tend to ship. Features that don't
tend to be redundant or premature, and live in the anti-scope list of
their candidate task driver until the answers sharpen.

## See also

- [`tasks/2026-05-10-v0.92-final-plan.md`](../tasks/2026-05-10-v0.92-final-plan.md) — current release driver
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — layered system architecture
- [`docs/PRESERVATION.md`](PRESERVATION.md) — capability invariants
- [`docs/EVAL_BASELINES.md`](EVAL_BASELINES.md) — retrieval-quality regression ledger
- [`docs/BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md) — operator guide to the processor
