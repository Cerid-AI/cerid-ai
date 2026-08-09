# Cerid AI — Development Roadmap

> **Last updated:** 2026-08-09.
> **Shipped:** v1.0.1 GA (August 2026) — see [CHANGELOG.md](../CHANGELOG.md) and the
> [GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page, including the
> signed desktop builds on the
> [v1.0.1 desktop release](https://github.com/Cerid-AI/cerid-ai/releases/tag/v1.0.1-desktop).
> **Current focus:** Electron desktop runtime refresh, multi-user / enterprise
> foundations, an MCP registry listing, and the continued eval-quality program.

---

## What this roadmap is

v1.0 is shipped. This document tracks the post-GA direction: near-term focus areas,
then the deferred and competitive backlog. Items here are intentions, not commitments;
released work moves to the [CHANGELOG](../CHANGELOG.md).

## Current focus

- **Electron runtime refresh** — keep the desktop app's Electron/Node baseline current
  and streamline the signed-build release pipeline.
- **Multi-user / enterprise** — grow the scaffolded enterprise overlay (SSO / SAML,
  ABAC, advanced audit logging) into a supported multi-user deployment mode.
- **MCP ecosystem** — publish Cerid in the MCP server registry; expand the public
  benchmark suite.
- **Eval-quality program** — continued retrieval/verification measurement: honest
  published baselines, nightly eval floors, and instrument-quality fixes ahead of new
  feature work.

## Backlog

- Native, on-brand subscription-management UI (first-party, replacing the hosted portal
  deep-link).
- First-party plugin marketplace.
- NLI GPU path (upstream inference dependency).
- Chat-message virtualization default-flip (currently opt-in via env flag).
- Expanded file-type handling (code AST extraction, Markdown frontmatter), bulk-import
  enhancements (content triage, scheduled re-scan), and ingestion pipeline hardening.
- External-client backend polish: per-client LLM routing hints, namespaced/registered
  custom domains with descriptions, and an optional per-namespace audit store for
  pure-client workloads.

### Competitive backlog

- **Developer-API depth:** batch ingest, cursor pagination, rate-limit/cost response headers,
  streaming SDK methods (incl. streaming verification), and quality/eval endpoints — the
  ergonomics the "knowledge-backend for agents" segment now expects.
- **Retrieval depth:** relevance-score calibration with confidence intervals, adaptive
  reranking, sparse-retrieval activation as corpora grow, and a GPU verification path.
- **Onboarding:** a guided setup wizard with auto-tuning — the single biggest adoption lever
  for a self-hosted product.
- **Pro depth:** an "always-fresh knowledge" staleness/provenance dashboard; a scheduled
  "knowledge health" verification report; multi-modal capture (audio/video transcription,
  image OCR); and a governance/audit layer for teams.
- **Packaging:** a team tier and usage-based pricing for the API-backend audience, alongside
  the existing solo Pro; Apple App Store / TestFlight distribution.

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
