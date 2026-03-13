# Design: K5 Digest View + K6 Batch Triage UI

> **Date:** 2026-03-13
> **Status:** Approved
> **Effort:** ~6–9 hrs total (K5 ~2–3 hrs, K6 ~4–6 hrs)
> **Mockup:** `src/web/public/mockup-k5-k6.html`

---

## Context

Two deferred backlog items (from Phase 16–18 plan) identified as core-enhancing features
for the "self-hosted Personal AI Knowledge Companion" premise:

- **K5 (Digest View):** Users need visibility into what knowledge was ingested and how it
  connects. Backend API exists (`GET /digest`), but no UI surfaces it.
- **K6 (Batch Triage UI):** Users ingest documents in batches. Backend supports parallel
  batch triage (`POST /agent/triage/batch`), but the upload dialog lacks batch controls.

---

## K5: Digest View

### Location

Integrated into the existing **Monitoring pane** (`monitoring-pane.tsx`) as the first
card — above `HealthCards`. Not a new sidebar pane.

### Component: `DigestCard`

**File:** `src/web/src/components/monitoring/digest-card.tsx`

**Data source:** `GET /digest?hours={24|72|168}`

**Response shape (from `src/mcp/routers/digest.py`):**

```typescript
interface DigestResponse {
  period_hours: number
  generated_at: string
  artifacts: {
    count: number
    items: Array<{
      id: string
      filename: string
      domain: string
      summary: string       // truncated to 100 chars
      ingested_at: string   // ISO 8601
    }>
    by_domain: Record<string, number>
  }
  relationships: { new_count: number }
  health: MaintenanceHealth  // reuse existing type
  recent_events: number
}
```

**Sections:**

1. **Header** — "Knowledge Digest" + time period dropdown (24h / 3d / 7d) + last-updated
   timestamp
2. **Summary stats** — 4 counters: Artifacts, Domains, Relationships, Events
3. **Domain breakdown** — pill badges with counts per domain (reuse domain color palette)
4. **Recent artifacts** — compact list (max 10 visible): filename + domain badge +
   relative time. "Show all" expands to full list.

**Data fetching:** `useQuery(["digest", hours], () => fetchDigest(hours), { refetchInterval: 60_000 })`

**Empty state:** "No activity in the last {period}." with muted icon.

### New code

| File | Change |
|------|--------|
| `src/web/src/lib/types.ts` | Add `DigestResponse` interface |
| `src/web/src/lib/api.ts` | Add `fetchDigest(hours)` function |
| `src/web/src/components/monitoring/digest-card.tsx` | New component |
| `src/web/src/components/monitoring/monitoring-pane.tsx` | Import + render DigestCard above HealthCards |
| `src/web/src/__tests__/digest-card.test.ts` | Unit tests |

---

## K6: Batch Triage UI

### Location

Extends the existing **UploadDialog** in the Knowledge pane. When ≥3 files are selected,
the dialog switches to batch mode with per-file controls.

### Approach: Enhanced UploadDialog

**File:** `src/web/src/components/kb/upload-dialog.tsx` (modify existing)

**Threshold:** 3+ files activates batch mode. ≤2 files keeps current simple behavior.

### Batch Mode UX

**Pre-upload state:**

1. **Defaults bar** — Domain select (Auto-detect default) + Categorization mode select
   (Smart/Pro/Manual). Applies to all files unless individually overridden.
2. **File table** — Rows: checkbox · filename · size · domain badge · status ("Ready")
3. **Per-file override** — Click domain badge to change individual file's domain
4. **Footer** — Cancel + "Start Batch Triage →"

**Processing state:**

- File rows update status: Ready → Processing (spinner) → Success/Duplicate/Failed
- Progress indicator at top: "Processing 3 of 6..."

**Completed state:**

- Summary banner: "✓ X succeeded · Y duplicates · Z failed"
- File rows show: status icon · filename · chunk count · domain · status badge
- Footer: "View in Knowledge Base" + "Done"

### Data source

**Request:** `POST /agent/triage/batch`

```typescript
interface TriageBatchRequest {
  files: Array<{
    file_path: string
    domain?: string
    categorize_mode?: string
    tags?: string
  }>
  default_mode?: string
}
```

**Response:**

```typescript
interface TriageBatchResponse {
  total: number
  succeeded: number
  failed: number
  duplicates: number
  results: Array<{
    filename: string
    status: "success" | "duplicate" | "error"
    artifact_id?: string
    domain?: string
    chunks?: number
    error?: string
  }>
}
```

**Note:** The batch endpoint expects `file_path` (server-side path), not uploaded file
blobs. The current upload flow uses `POST /ingest_file` with multipart form data. The
batch UI will need to:

1. Upload files first (existing `uploadFile()` API)
2. Then call batch triage on the uploaded paths

OR: Extend the batch endpoint to accept multipart uploads. This needs investigation
during implementation.

### New code

| File | Change |
|------|--------|
| `src/web/src/lib/types.ts` | Add `TriageBatchResponse` interface |
| `src/web/src/lib/api.ts` | Add `triageBatch()` function |
| `src/web/src/components/kb/upload-dialog.tsx` | Add batch mode (≥3 files) |
| `src/web/src/components/kb/knowledge-pane.tsx` | Wire batch results to ingestion log |
| `src/web/src/__tests__/upload-dialog.test.ts` | Batch mode unit tests |

---

## Testing Strategy

- **Unit tests:** Render each component with mocked API data, verify states (loading,
  empty, populated, error). Test batch mode threshold (2 vs 3 files).
- **Integration:** Verify `fetchDigest` and `triageBatch` API functions against response
  shapes.
- **Manual:** Test with live backend — ingest files, check digest updates, run batch
  upload of 5+ files.

---

## Out of Scope

- Digest email/notification scheduling
- Digest export (PDF/CSV)
- Per-file tag editing in batch mode (deferred — use defaults only)
- Plugin management UI (K4 — infrastructure, not core)
- Codecov, license scanning, ReDoS audit (K1/K2/K3 — infrastructure)
