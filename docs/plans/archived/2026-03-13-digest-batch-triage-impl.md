# K5 Digest View + K6 Batch Triage UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Knowledge Digest card to the Monitoring pane and enhance the Upload Dialog with batch mode (per-file status, domain overrides) for 3+ file uploads.

**Architecture:** K5 adds a new `DigestCard` component fetching from the existing `GET /digest` backend. K6 enhances the existing `UploadDialog` with batch-aware state tracking — the current `Promise.allSettled` parallel upload in `knowledge-pane.tsx` already works, but the UI doesn't show per-file progress. Both features add types to `types.ts`, API functions to `api.ts`, and tests.

**Tech Stack:** React 19, TypeScript, TanStack React Query, shadcn/ui, Vitest, Tailwind v4

---

## Task 1: Add DigestResponse type

**Files:**
- Modify: `src/web/src/lib/types.ts` (append after line ~207, after `MaintenanceHealth`)

**Step 1: Add the type definition**

After the existing `MaintenanceHealth` interface (which ends around line 207), add:

```typescript
export interface DigestArtifact {
  id: string
  filename: string
  domain: string
  summary: string
  ingested_at: string
}

export interface DigestResponse {
  period_hours: number
  generated_at: string
  artifacts: {
    count: number
    items: DigestArtifact[]
    by_domain: Record<string, number>
  }
  relationships: { new_count: number }
  health: MaintenanceHealth
  recent_events: number
}
```

**Step 2: Verify typecheck passes**

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean (no errors)

**Step 3: Commit**

```
git add src/web/src/lib/types.ts
git commit -m "feat(types): add DigestResponse interface for K5 digest view"
```

---

## Task 2: Add fetchDigest API function

**Files:**
- Modify: `src/web/src/lib/api.ts` (add after `fetchMaintenance`)

**Step 1: Add the fetch function**

Find `fetchMaintenance` in `api.ts` and add after it:

```typescript
export async function fetchDigest(hours = 24): Promise<DigestResponse> {
  const res = await fetch(`${MCP_BASE}/digest?hours=${hours}`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Digest fetch failed: ${res.status}`))
  return res.json()
}
```

Add `DigestResponse` to the import from `@/lib/types` at the top of the file.

**Step 2: Verify typecheck passes**

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean

**Step 3: Commit**

```
git add src/web/src/lib/api.ts
git commit -m "feat(api): add fetchDigest function for K5 digest view"
```

---

## Task 3: Write DigestCard tests

**Files:**
- Create: `src/web/src/__tests__/digest-card.test.ts`

**Step 1: Write the failing tests**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { DigestCard } from "@/components/monitoring/digest-card"
import type { DigestResponse } from "@/lib/types"

// Mock the API module
vi.mock("@/lib/api", () => ({
  fetchDigest: vi.fn(),
}))

const MOCK_DIGEST: DigestResponse = {
  period_hours: 24,
  generated_at: new Date().toISOString(),
  artifacts: {
    count: 12,
    items: [
      { id: "a1", filename: "report.pdf", domain: "finance", summary: "Quarterly report", ingested_at: new Date(Date.now() - 3600000).toISOString() },
      { id: "a2", filename: "auth.py", domain: "code", summary: "Auth middleware", ingested_at: new Date(Date.now() - 7200000).toISOString() },
      { id: "a3", filename: "paper.md", domain: "research", summary: "LLM patterns", ingested_at: new Date(Date.now() - 10800000).toISOString() },
    ],
    by_domain: { finance: 5, code: 4, research: 3 },
  },
  relationships: { new_count: 8 },
  health: {
    overall: "healthy",
    services: { chromadb: "connected", neo4j: "connected" },
    data: { collections: 4, total_chunks: 500, collection_sizes: {}, artifacts: 120, domains: 4, audit_log_entries: 200 },
  },
  recent_events: 47,
}

const EMPTY_DIGEST: DigestResponse = {
  period_hours: 24,
  generated_at: new Date().toISOString(),
  artifacts: { count: 0, items: [], by_domain: {} },
  relationships: { new_count: 0 },
  health: {
    overall: "healthy",
    services: {},
    data: { collections: 0, total_chunks: 0, collection_sizes: {}, artifacts: 0, domains: 0, audit_log_entries: 0 },
  },
  recent_events: 0,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe("DigestCard", () => {
  it("renders summary stats from digest data", () => {
    render(<DigestCard digest={MOCK_DIGEST} isLoading={false} />, { wrapper })
    expect(screen.getByText("12")).toBeInTheDocument()       // artifact count
    expect(screen.getByText("8")).toBeInTheDocument()         // relationships
    expect(screen.getByText("47")).toBeInTheDocument()        // events
  })

  it("renders domain breakdown pills", () => {
    render(<DigestCard digest={MOCK_DIGEST} isLoading={false} />, { wrapper })
    expect(screen.getByText("finance")).toBeInTheDocument()
    expect(screen.getByText("code")).toBeInTheDocument()
    expect(screen.getByText("research")).toBeInTheDocument()
  })

  it("renders recent artifact filenames", () => {
    render(<DigestCard digest={MOCK_DIGEST} isLoading={false} />, { wrapper })
    expect(screen.getByText("report.pdf")).toBeInTheDocument()
    expect(screen.getByText("auth.py")).toBeInTheDocument()
    expect(screen.getByText("paper.md")).toBeInTheDocument()
  })

  it("renders empty state when no artifacts", () => {
    render(<DigestCard digest={EMPTY_DIGEST} isLoading={false} />, { wrapper })
    expect(screen.getByText(/no activity/i)).toBeInTheDocument()
  })

  it("renders loading state", () => {
    render(<DigestCard digest={undefined} isLoading={true} />, { wrapper })
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it("renders period selector with 24h default", () => {
    render(<DigestCard digest={MOCK_DIGEST} isLoading={false} />, { wrapper })
    expect(screen.getByText(/24/)).toBeInTheDocument()
  })
})
```

**Step 2: Run tests to verify they fail**

Run: `cd src/web && npx vitest run src/__tests__/digest-card.test.ts`
Expected: FAIL — `DigestCard` module not found

**Step 3: Commit failing tests**

```
git add src/web/src/__tests__/digest-card.test.ts
git commit -m "test: add failing DigestCard tests for K5"
```

---

## Task 4: Implement DigestCard component

**Files:**
- Create: `src/web/src/components/monitoring/digest-card.tsx`

**Step 1: Implement the component**

```tsx
import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { BookOpen, Loader2, Inbox } from "lucide-react"
import type { DigestResponse } from "@/lib/types"

const PERIOD_OPTIONS = [
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
] as const

interface DigestCardProps {
  digest: DigestResponse | undefined
  isLoading: boolean
  onPeriodChange?: (hours: number) => void
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export function DigestCard({ digest, isLoading, onPeriodChange }: DigestCardProps) {
  const [period, setPeriod] = useState("24")

  const handlePeriodChange = (value: string) => {
    setPeriod(value)
    onPeriodChange?.(Number(value))
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <BookOpen className="h-4 w-4" /> Knowledge Digest
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="ml-2 text-sm text-muted-foreground">Loading digest…</span>
        </CardContent>
      </Card>
    )
  }

  const empty = !digest || digest.artifacts.count === 0

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <BookOpen className="h-4 w-4" /> Knowledge Digest
        </CardTitle>
        <Select value={period} onValueChange={handlePeriodChange}>
          <SelectTrigger className="h-7 w-[140px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PERIOD_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>

      <CardContent className="space-y-4">
        {empty ? (
          <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
            <Inbox className="mb-2 h-8 w-8" />
            <p className="text-sm">No activity in the last {period === "24" ? "24 hours" : period === "72" ? "3 days" : "7 days"}.</p>
          </div>
        ) : (
          <>
            {/* Summary Stats */}
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: "Artifacts", value: digest.artifacts.count },
                { label: "Domains", value: Object.keys(digest.artifacts.by_domain).length },
                { label: "Relationships", value: digest.relationships.new_count },
                { label: "Events", value: digest.recent_events },
              ].map((stat) => (
                <div key={stat.label} className="text-center">
                  <div className="text-2xl font-bold tabular-nums">{stat.value}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>

            {/* Domain Breakdown */}
            <div>
              <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                Domain Distribution
              </p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(digest.artifacts.by_domain).map(([domain, count]) => (
                  <Badge key={domain} variant="outline" className="gap-1 text-xs">
                    <span className="font-bold tabular-nums">{count}</span>
                    <span>{domain}</span>
                  </Badge>
                ))}
              </div>
            </div>

            {/* Recent Artifacts */}
            <div>
              <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                Recent Artifacts
              </p>
              <div className="space-y-1">
                {digest.artifacts.items.slice(0, 10).map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
                  >
                    <span className="min-w-0 flex-1 truncate font-medium">{item.filename}</span>
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {item.domain}
                    </Badge>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {formatRelativeTime(item.ingested_at)}
                    </span>
                  </div>
                ))}
              </div>
              {digest.artifacts.items.length > 10 && (
                <p className="mt-1 text-center text-[10px] text-muted-foreground">
                  {digest.artifacts.items.length - 10} more…
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
```

**Step 2: Run tests to verify they pass**

Run: `cd src/web && npx vitest run src/__tests__/digest-card.test.ts`
Expected: All 6 tests PASS

**Step 3: Commit**

```
git add src/web/src/components/monitoring/digest-card.tsx
git commit -m "feat: implement DigestCard component for K5 digest view"
```

---

## Task 5: Integrate DigestCard into MonitoringPane

**Files:**
- Modify: `src/web/src/components/monitoring/monitoring-pane.tsx`

**Step 1: Add digest query and component**

Add import at top:
```typescript
import { DigestCard } from "./digest-card"
```

Add to `fetchMaintenance` import line in `@/lib/api`:
```typescript
import { fetchMaintenance, fetchIngestLog, fetchSchedulerStatus, fetchDigest } from "@/lib/api"
```

Add a new `useQuery` hook after the existing three hooks:
```typescript
const [digestHours, setDigestHours] = useState(24)
const { data: digest, isLoading: loadingDigest } = useQuery({
  queryKey: ["digest", digestHours],
  queryFn: () => fetchDigest(digestHours),
  refetchInterval: 60_000,
})
```

Add `useState` to the React import at the top.

Insert `DigestCard` as the first item inside the `<div className="space-y-4 p-4">` section, before HealthCards:
```tsx
<PaneErrorBoundary label="Knowledge Digest">
  <DigestCard digest={digest} isLoading={loadingDigest} onPeriodChange={setDigestHours} />
</PaneErrorBoundary>
```

**Step 2: Verify typecheck passes**

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean

**Step 3: Run all digest tests**

Run: `cd src/web && npx vitest run src/__tests__/digest-card.test.ts`
Expected: All PASS

**Step 4: Commit**

```
git add src/web/src/components/monitoring/monitoring-pane.tsx
git commit -m "feat: integrate DigestCard into Monitoring pane"
```

---

## Task 6: Write Batch Upload Dialog tests

**Files:**
- Modify: `src/web/src/__tests__/upload-dialog.test.ts` (add batch tests after existing tests)

**Step 1: Check existing test file**

Read `src/web/src/__tests__/upload-dialog.test.ts` first to see current test patterns and add
batch-specific tests that complement (not duplicate) existing coverage.

**Step 2: Add batch mode tests**

Append after existing tests in the describe block:

```typescript
describe("batch mode (≥3 files)", () => {
  const batchFiles = [
    new File(["a"], "report.pdf", { type: "application/pdf" }),
    new File(["b"], "code.py", { type: "text/plain" }),
    new File(["c"], "notes.md", { type: "text/markdown" }),
  ]

  it("shows batch header with file count when ≥3 files", () => {
    render(<UploadDialog files={batchFiles} defaultDomain={null} onConfirm={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    expect(screen.getByText(/3 files/i)).toBeInTheDocument()
  })

  it("renders per-file rows with filenames", () => {
    render(<UploadDialog files={batchFiles} defaultDomain={null} onConfirm={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    expect(screen.getByText("report.pdf")).toBeInTheDocument()
    expect(screen.getByText("code.py")).toBeInTheDocument()
    expect(screen.getByText("notes.md")).toBeInTheDocument()
  })

  it("shows defaults bar with domain and mode selectors", () => {
    render(<UploadDialog files={batchFiles} defaultDomain={null} onConfirm={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    // Should show two select triggers (domain + mode) in batch mode
    const triggers = screen.getAllByRole("combobox")
    expect(triggers.length).toBeGreaterThanOrEqual(2)
  })

  it("calls onConfirm with options when Start Batch clicked", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(<UploadDialog files={batchFiles} defaultDomain={null} onConfirm={onConfirm} onCancel={vi.fn()} />, { wrapper })
    const startBtn = screen.getByRole("button", { name: /upload|batch|start/i })
    await user.click(startBtn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it("keeps simple mode for ≤2 files", () => {
    const twoFiles = batchFiles.slice(0, 2)
    render(<UploadDialog files={twoFiles} defaultDomain={null} onConfirm={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    // Should show "Upload 2 Files" not batch mode
    expect(screen.getByText(/upload 2 files/i)).toBeInTheDocument()
  })
})
```

**Step 3: Run to verify failures**

Run: `cd src/web && npx vitest run src/__tests__/upload-dialog.test.ts`
Expected: New batch tests FAIL (batch mode not implemented yet), existing tests still pass

**Step 4: Commit**

```
git add src/web/src/__tests__/upload-dialog.test.ts
git commit -m "test: add failing batch mode tests for K6 upload dialog"
```

---

## Task 7: Implement batch mode in UploadDialog

**Files:**
- Modify: `src/web/src/components/kb/upload-dialog.tsx`

**Step 1: Enhance the component**

The key changes to `upload-dialog.tsx`:

1. Add a `BATCH_THRESHOLD = 3` constant
2. Add `isBatch = files.length >= BATCH_THRESHOLD` derived state
3. When `isBatch`:
   - Show an expanded file list with individual file sizes in a table layout
   - Keep the same domain/categorization selects (they serve as defaults)
   - Change button text to "Start Batch" instead of "Upload N Files"
4. When not batch, keep current behavior unchanged

The dialog already accepts multiple files and passes `domain`/`categorize_mode` through `onConfirm`. The batch enhancement is purely visual — the `knowledge-pane.tsx` `handleUploadConfirm` already uses `Promise.allSettled` for parallel upload.

Replace the file list section. When `isBatch`, render a table with columns: filename, size, domain badge. When not, keep the current compact list.

Key implementation detail: **no changes to `onConfirm` signature needed** — it already passes `{ domain, categorize_mode }` which applies to all files. Per-file domain overrides can be added as a follow-up (YAGNI for now — the mockup shows it but it adds complexity without clear demand).

**Step 2: Run tests**

Run: `cd src/web && npx vitest run src/__tests__/upload-dialog.test.ts`
Expected: All tests PASS (existing + new batch tests)

**Step 3: Typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean

**Step 4: Commit**

```
git add src/web/src/components/kb/upload-dialog.tsx
git commit -m "feat: add batch mode to UploadDialog for K6 (≥3 files)"
```

---

## Task 8: Add batch upload progress tracking to KnowledgePaneq

**Files:**
- Modify: `src/web/src/components/kb/knowledge-pane.tsx`

**Step 1: Enhance handleUploadConfirm with per-file tracking**

Currently `handleUploadConfirm` (around line 129) uses `Promise.allSettled` and reports a
single aggregate status. Enhance it to:

1. Add a `batchResults` state: `useState<Array<{ name: string; status: "pending" | "uploading" | "success" | "duplicate" | "error"; chunks?: number }>>([])`
2. Before uploading, initialize `batchResults` from `pendingFiles` with all statuses = "pending"
3. As each file completes, update its entry in `batchResults`
4. Pass `batchResults` and a `batchDone` flag to the UploadDialog so it can show per-file progress in the completed state

This requires extending UploadDialog props to optionally accept `batchResults` for the results view.

**Step 2: Run tests**

Run: `cd src/web && npx vitest run`
Expected: All tests pass

**Step 3: Typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean

**Step 4: Commit**

```
git add src/web/src/components/kb/knowledge-pane.tsx src/web/src/components/kb/upload-dialog.tsx
git commit -m "feat: add per-file batch progress tracking to upload flow"
```

---

## Task 9: Clean up mockup file and update docs

**Files:**
- Delete: `src/web/public/mockup-k5-k6.html`
- Modify: `docs/ISSUES.md` (mark K5 and K6 as resolved)
- Modify: `CLAUDE.md` (update test count)
- Modify: `tasks/todo.md` (mark items done)

**Step 1: Remove mockup**

```bash
rm src/web/public/mockup-k5-k6.html
```

**Step 2: Update ISSUES.md**

Mark K5 and K6 as resolved with today's date. Update header counts.

**Step 3: Update CLAUDE.md**

Update frontend test count to reflect new tests added.

**Step 4: Run full test suite**

Run: `cd src/web && npx vitest run`
Expected: All tests pass. Note the new count.

Run: `cd src/web && npx tsc --noEmit`
Expected: Clean

**Step 5: Commit**

```
git add -A
git commit -m "chore: resolve K5 + K6, update docs, remove mockup"
```

---

## Task 10: Push and verify CI

**Step 1: Push**

```bash
git push
```

**Step 2: Monitor CI**

Check GitHub Actions for green status on all 7 jobs.

---

## Summary

| Task | Description | Est. |
|------|-------------|------|
| 1 | DigestResponse type | 5 min |
| 2 | fetchDigest API function | 5 min |
| 3 | DigestCard tests (failing) | 10 min |
| 4 | DigestCard implementation | 20 min |
| 5 | Integrate into MonitoringPane | 10 min |
| 6 | Batch upload tests (failing) | 10 min |
| 7 | Batch mode in UploadDialog | 30 min |
| 8 | Per-file progress tracking | 30 min |
| 9 | Docs + cleanup | 10 min |
| 10 | Push + CI verification | 5 min |
| **Total** | | **~2.5 hrs** |
