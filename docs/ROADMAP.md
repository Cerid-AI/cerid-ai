# Cerid AI — Development Roadmap

> **Last updated:** 2026-08-30 (1.0.3 shipped; v1.0 release arc closed. Status authority:
> [`docs/superpowers/specs/2026-08-05-v1-ga-audit-and-remediation.md`](superpowers/specs/2026-08-05-v1-ga-audit-and-remediation.md).
> Prior: 2026-07-24 Tier A audit + remediation T0–T4; 2026-06-13 RAG Quality Program
> close-out; 2026-06-02 Commercial-GA Track 1).
> **Shipped:** `v1.0.3` tagged 2026-08-30 (both repos) with `v1.0.3-desktop`
> signed + notarized (macOS universal DMG + Windows installer), now Latest on the
> public repo where the updater polls; previously `v1.0.2` 2026-08-15 and
> `v1.0.0` + `v1.0.1` 2026-08-07. `v1.0.1-desktop`
> was WITHDRAWN 2026-08-16 — its binaries could not launch (restricted
> entitlements, no provisioning profile); the tag remains, the release does not.
> SDKs live on PyPI (`cerid-sdk`) + npm (`@cerid-ai/sdk`), trusted publishing verified
> 2026-08-09. The Electron runtime refresh CLOSED in 1.0.3 (33 → 43, universal
> build, native modules rebuilt per-arch). Forward focus: Sentry go-live (web DSN +
> alert thresholds), the soak/beta validation program, multi-user, and CI coverage
> for the widget/SDK bundles — which still have no build or test job.
> **Currently shipped:** Atlas (decomposition icicle) + Constellation cartographic map,
> Subjects/Sources/Settings consolidation (4-pane), the registry-driven Settings redesign
> (SEXTANT) and the Subjects UX cycles (TRELLIS domain backbone / Tephra timeline / FOLIO
> wiki / STRATA atlas), Apple ecosystem baseline, meeting-capture runtime, cloud
> connectors, Swift CLI helpers, metamorphic verification, Custom Smart RAG, AI inbox
> triage, daily digest, advanced analytics, Tour, the Knowledge
> Architecture program, Pro billing/checkout + license activation. 60 MCP tools (55 with
> trading disabled), 8,000+ Python / 2,700+ frontend / 250+ preservation-marked tests
> (snapshots drift — re-derive before quoting). Full ledger:
> [`docs/COMPLETED_PHASES.md`](COMPLETED_PHASES.md).
> **Shipped releases:** [CHANGELOG.md](../CHANGELOG.md) and the
> [GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page.

---

## What this roadmap is

The product is feature-complete. The work that remains is **commercial hardening** —
making it possible to *sell and manage* Cerid Pro, making the Pro feature surface
*truthful and regression-locked*, and making the release *operationally ready* — followed
by validation and launch. It is organized as three tracks.

- **Track 1 — Commercial GA** (primary near-term driver): Pro sale, Pro management, and
  Pro/billing hardening. This is the critical path to dropping the `-rc` suffix.
- **Track 2 — Validation & launch:** soak window, observability go-live, and the
  go-to-market items that gate the public release.
- **Track 3 — Post-GA / v1.1:** deferred enhancements that don't block the first
  commercial release.

Priority legend: **P0** blocker · **P1** high · **P2** medium · **P3** low.

---

## Track 1 — Commercial GA

The Pro tier's *features* are largely built and gated; this track makes the *commerce
around them* production-grade and the *gating* trustworthy end-to-end.

### P0 — Pro feature truth-up & gating lock-in — ✅ Landed (2026-06-01)
Make the tier system tell the truth and hold it against regression.
*Shipped: connector loader fixed so class-based plugins load and gate (with a boot test);
gating allowlist pruned to the genuinely-pending features; previously-ungated analytics now
gated; the feature/tier matrix is generated from the source of truth and drift-gated in CI;
the Pro settings pane renders live from the capabilities API.*
- Reconcile the runtime-gating ledger so every shipped Pro feature is gate-asserted by the
  gating lint (no silently-ungated paid features, no stale exemptions).
- Close the remaining gating gaps on Pro surfaces that are reachable without a tier check.
- Verify the connector plugins load and gate as intended under the plugin loader (a
  load-correctness pass, with a boot test so it can't regress).
- Generate the public **feature/tier matrix** from the single source of truth and gate it
  in CI, so it can never drift from the code again.
- Drive the Pro settings pane from the live capabilities API rather than a hand-maintained
  list, so the UI never lies about what a tier unlocks.

### P0 — Pro subscription self-service
Let a customer buy *and* manage their subscription without operator intervention.
- **Stripe Customer Portal** integration for GA: customers update payment method, view and
  download invoices, and cancel or change plan from a hosted, PCI-offloaded surface.
- Enrich subscription status (renewal date, trial end, plan, cancellation state) so the
  app answers basic billing questions without a support ticket.
- (A fully native, on-brand management UI is a v1.1 follow-on — see Track 3.)

### P0 — License lifecycle & operator administration
Make licenses safe to issue, revoke, and audit.
- Operator tooling to issue licenses (trials, B2B, sales overrides), revoke with hard
  enforcement (chargeback/fraud), refund, and audit the full lifecycle.
- License **expiry enforcement** and **seat/device binding**, with an offline-activation
  fallback for disconnected installs.
- A single canonical license-verification path.

### P1 — Complete the Pro Apple connector suite
Land the remaining Apple-native readers so the advertised Pro suite is whole at GA.
- Apple Mail, iMessage, and Reminders readers (joining the already-shipped Apple Notes,
  Calendar, and Photos), each behind its feature gate and TCC/Full-Disk-Access consent.
- iMessage content honors Private Mode (Level 2+) at query time.

### P0 — Stripe live-mode hardening & billing observability
- Document and validate all billing/licensing configuration; pin the Stripe API version.
- Webhook idempotency hardened and proven; checkout protected against double-submit.
- Billing-path error monitoring + payment-failure alerting.
- Operational runbook for the test→live migration and the GA charge/refund proof.

### P0 — External agent / client backend support — ✅ Landed (2026-06-01)
Make Cerid usable as a shared knowledge + LLM + memory backend for other applications
without per-client shims — a GA-required surface, not a v1.1 follow-on. *All five points
below shipped and are canary-validated; the "Cerid as a backend" guide is published in
[`docs/SDK_GUIDE.md`](SDK_GUIDE.md).*
- **First-class custom knowledge domains:** clients ingest to and query their own domain
  names without pre-registration (today only built-in / env-registered domains pass
  validation); an unknown-but-unused domain degrades to empty results, never an error.
- **Rich provenance metadata** preserved end-to-end on the SDK ingest path (today only
  tags persist there), so client artifacts keep their attribution through retrieval.
- **Flexible LLM task types:** accept client-defined task-type labels, mapping unknown
  ones to safe internal routing rather than failing.
- **Custom domains first-class in retrieval and ranking**, with client-domain activity
  surfaced in health (no false "empty-collection" alerts).
- A complete, documented **"Cerid as a backend" SDK surface** (most endpoints already
  ship; closes the ingest-metadata gap) plus an integration guide, validated by a
  reference external client running end-to-end with no compatibility shims. See
  [`docs/SDK_GUIDE.md`](SDK_GUIDE.md).

### P0 — Knowledge-architecture depth & retrieval quality  _(added 2026-06-02)_
Make the differentiator real and measured before GA, not just described.

> **Status 2026-06-13:** largely landed. Artifact-level fusion + low-confidence
> signals + the NLI-faithfulness benchmark shipped 2026-06-05 (see
> `docs/GA_CHECKLIST.md`, `docs/NLI_FAITHFULNESS_BENCHMARK.md`). The **RAG Quality
> Program** (Slices 1–7, 2026-06-12/13 — see `docs/COMPLETED_PHASES.md`) advanced
> this P0 further: salience-weighted taxonomy, personal-first pack ranking,
> stale-evidence verification, provenance end-to-end, with retrieval recall
> validated at 1.0 (no regression) at Eval Checkpoint 2. Surface routing is live
> per intent; episodic-memory recall lift remains a soak-measured outcome.
- **All four knowledge surfaces first-class in the query path** — unify routing so wiki,
  vector, graph, and episodic-memory are selected per intent on the live query path (not only
  surfaced as observability), with the chosen surface returned in the response; wire
  episodic-memory auto-recall for personal-context queries.
- **Retrieval-quality validation** — artifact-level rank fusion; honest, published retrieval
  and verification benchmarks as the release floor; surface low-confidence and
  empty-collection signals so the UI never silently returns nothing.
- **Idempotent ingest** across the SDK write surface, completing the external-backend story.
- **Verification, evidenced** — publish the NLI-faithfulness benchmark that substantiates the
  trust differentiator (no competitor ships entailment-gated retrieval).

---

## Track 2 — Validation & launch

- **14-day soak** of the release candidate in staging; collect the K-program success
  metrics (wiki coverage, staleness, faithfulness, chunks-per-answer, memory→entity
  linkage, contradiction surfacing) and make the concept-pages activate/close decision.
- **Observability go-live:** production frontend error-monitoring DSN provisioned; alert
  thresholds configured (error rate, p99 latency, health-degradation).
- **Go-to-market (business-gated):** pricing page (`$15/mo · $144/yr · 14-day trial`),
  60–90s demo video (Constellation hero), Apple App Store / TestFlight submission.
  (Shipped from this bullet: the `v1.0.0` tag, 2026-08-07; launch comms drafted
  2026-08-09 — posting still pending.)
  **Demo video (v4, 2026-08-16):** 90s + 30s live-product sizzle on [cerid.ai/#demo](https://cerid.ai/#demo)
  — verify/refute, snappy Constellation explore, wiki, TrustScore, feature cards.
  Hosted from `cerid-ai-marketing/public/`; rebuild via local `docs/assets/demo-video/` pipeline.

---

## Track 3 — Post-GA / v1.1

- Native, on-brand subscription-management UI (first-party, replacing the hosted portal
  deep-link).
- First-party plugin marketplace.
- SSO / SAML and advanced audit logging (Enterprise tier — currently scaffolded).
- NLI GPU path (upstream inference dependency).
- Chat-message virtualization default-flip (currently opt-in via env flag).
- Expanded file-type handling (code AST extraction, Markdown frontmatter), bulk-import
  enhancements (content triage, scheduled re-scan), and ingestion pipeline hardening.
- External-client backend **polish** (the GA core ships in Track 1): per-client LLM
  routing hints, namespaced/registered custom domains with descriptions, and an optional
  per-namespace audit store for pure-client workloads.

### Competitive backlog _(from the 2026-06-02 market study + audits)_
- **Developer-API depth:** batch ingest, cursor pagination, rate-limit/cost response headers,
  streaming SDK methods (incl. streaming verification), and quality/eval endpoints — the
  ergonomics the "knowledge-backend for agents" segment now expects.
- **Retrieval depth:** relevance-score calibration with confidence intervals, adaptive
  reranking, sparse-retrieval activation as corpora grow, and a GPU verification path.
- **Onboarding:** a guided setup wizard with auto-tuning — the single biggest adoption lever
  for a self-hosted product.
- **Ecosystem:** publish Cerid in the MCP server registry; expand the public benchmark suite.
- **Pro depth:** an "always-fresh knowledge" staleness/provenance dashboard; a scheduled
  "knowledge health" verification report; multi-modal capture (audio/video transcription,
  image OCR); and a governance/audit layer for teams.
- **Packaging:** a team tier and usage-based pricing for the API-backend audience, alongside
  the existing solo Pro.

---

## Tiers

| Tier | License | Audience | Price |
|---|---|---|---|
| **Cerid Core** | FSL-1.1-ALv2 (source-available) | Developers, researchers, personal use | Free |
| **Cerid Pro** | BUSL-1.1 | Business and power users | $15/mo · $144/yr |
| **Cerid Enterprise** | Commercial | Regulated and large organizations | Contact |

The full feature/tier breakdown lives in
[`docs/TIER_MATRIX.md`](TIER_MATRIX.md). Released work is tracked in
[CHANGELOG.md](../CHANGELOG.md) and the
[GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page.
