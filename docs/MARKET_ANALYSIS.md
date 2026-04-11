# Cerid AI — Market Analysis: Claims vs Reality

> **Date:** 2026-03-30
> **Purpose:** Critical evaluation of marketing claims vs actual product delivery
> **Methodology:** Cross-referencing marketing site copy with codebase implementation

## Scorecard

| Metric | Score | Notes |
|--------|-------|-------|
| **Feature Completeness** | 72% | Core pipeline excellent, enterprise features incomplete |
| **Marketing Accuracy** | 58% | Technical details accurate, headline claims overstated |

## Claim-by-Claim Assessment

| Marketing Claim | Status | Assessment |
|----------------|--------|------------|
| 30+ File Types | **ACCURATE** | 37 extensions registered; ~28 use generic text parser, specialized parsing for PDF/DOCX/XLSX/EML/EPUB |
| 5 min Setup | **OVERSTATED** | Realistic for Docker-experienced developer; 30-60 min for general user (Docker install, age decryption, model keys) |
| Smart Retrieval | **ACCURATE** | 8-stage adaptive pipeline fully implemented with per-chunk profiles |
| Verified Answers | **PARTIALLY** | Architecture sound; was silently broken until 2026-03-29 model routing fix; not automatic in Simple mode |
| Any AI Model | **MISLEADING** | Routes through OpenRouter only (no direct provider SDKs); Chinese models blocked for compliance |
| Learns From You | **ACCURATE** | 6-type memory salience with 500ms timeout caveat (silent failure on cold Neo4j) |
| Totally Private | **PARTIALLY** | Sentry SDK initialized unconditionally; ChromaDB telemetry correctly disabled; claim "zero telemetry" contradicted |
| Easy Import | **ACCURATE** | Bulk import is the most polished feature; preview, progress, pause/resume all work |
| 10 AI Agents | **ACCURATE** | All 10 are genuine, functional modules |
| 26 MCP Tools | **TECHNICALLY ACCURATE** | 5 are trading-only, 1 requires Pro; core user has ~20 functional tools |
| Pro Features | **IMPLEMENTED but NOT PURCHASABLE** | All gated features work; no purchase mechanism exists |
| SSO/SAML (Vault) | **NOT IMPLEMENTED** | Feature flag exists, code comment says "no implementation yet" |
| Audit Logging (Vault) | **PARTIAL** | Redis activity log exists; no structured audit schema or SIEM export |

## Top 5 Priority Improvements

### 1. Setup Experience (High Impact)
- **Problem:** "5 min Setup" is unrealistic for most users
- **Fix:** Create a one-click installer script that handles Docker detection, key generation, and first-run. Add a web-based setup wizard that runs before the main app.
- **Marketing fix:** Change to "Quick Setup" or "Get running in minutes" without a specific number

### 2. SSO/SAML (Enterprise Blocker)
- **Problem:** Vault tier advertises SSO but has zero implementation
- **Fix:** Either implement SSO or change TIER_MATRIX and pricing page to mark it as "(planned)" — which was done in TIER_MATRIX but NOT on the pricing page
- **Priority:** If targeting enterprise, this is a deal-breaker

### 3. Telemetry Transparency (Trust Issue)
- **Problem:** "Zero telemetry" claim contradicted by Sentry SDK
- **Fix:** Make Sentry opt-in (only init when SENTRY_DSN is explicitly set AND a separate ENABLE_SENTRY=true flag). Update security page to "telemetry is optional and disabled by default"
- **Priority:** High for IC/USG audience

### 4. Pro Tier Commercial Path (Business Gap)
- **Problem:** Features are built but no way to purchase
- **Fix:** Either open a waitlist/early access program, or make all Pro features available in Core and differentiate on support/SLA instead
- **Priority:** Medium — blocks revenue

### 5. Verification Reliability (Feature Quality)
- **Problem:** Flagship feature had a silent model routing bug
- **Fix:** Add verification health check at startup that fires a test claim; show verification status in the health dashboard; add monitoring alert for consecutive extraction failures
- **Priority:** High — core value proposition
