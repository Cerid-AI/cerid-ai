# Pro — Advanced Analytics

Settings → Diagnostics → Analytics surfaces four visualizations on top
of the operational telemetry Cerid already collects. Two are visible
at the community tier; two are Pro-tier.

## What you see

### Trust score sunburst — free + Pro

A two-ring radial chart:

- **Center**: composite trust score (0-100) colored by band
  (high / medium / low / unavailable).
- **Outer ring**: six trust components — faithfulness, retrieval
  NDCG@10, memory recall, verification coverage, preservation health,
  user agreement — colored by status (ok / warn / fail / not_available).

Hover any segment for the component's current value vs. target. Click
"Drill down" for the full modal (the existing TrustScoreModal carries
per-component explanations + when-each-drops guidance).

Backend: `GET /observability/trust-score` (existing endpoint, no Phase
L changes).

### Knowledge growth heatmap — free + Pro

GitHub-style commit grid showing artifact ingest activity over the
last 365 days. Brand-teal intensity scale; darker = more ingests on
that day.

Clicking a day deep-links into Sources → Activity with a `?since=`
date filter. (The Activity pane wires the filter receiver in a small
follow-up — for now the URL state is correct; the activity feed
narrows when that lands.)

Backend: new `GET /analytics/ingestion-by-day?window_days=N`.

### Cost Sankey — Pro only

LLM cost flow over the last 30 days. Left column = pipeline phase
(ingest / retrieval / verification / curator / pro_features / other).
Right column = individual stage. Flow width is dollars spent.

Stage attribution: when LLM calls record `stage="..."` as a metric
tag, we use it directly. Stages that don't tag fall into the "other"
bucket so the total stays honest. Augmenting all `call_internal_llm`
sites to record stage tags is incremental work tracked separately.

Backend: new `GET /analytics/cost-by-stage?window_days=N`.

### Quality rolling timeline — Pro only

90-day rolling line chart of four signals:

| Metric | Source | Target |
|---|---|---|
| NDCG@10 | nightly retrieval eval | ≥ baseline |
| Faithfulness | nightly RAGAS | ≥ 0.90 |
| Memory recall | weekly LongMemEval | ≥ 0.80 |
| Verification accuracy | rolling 7d NLI | ≥ 0.95 |

Days without samples render as gaps (more honest than zero-fill).

Backend: new `GET /analytics/quality-timeline?window_days=N`.

## REST surface

```bash
# Ingestion heatmap data (max 730 days window)
curl http://localhost:8888/analytics/ingestion-by-day?window_days=365

# Cost Sankey nodes + edges
curl http://localhost:8888/analytics/cost-by-stage?window_days=30

# Quality timeline aggregated to daily buckets
curl http://localhost:8888/analytics/quality-timeline?window_days=90
```

All three are open endpoints (no auth gate yet). The Pro restriction
is enforced UI-side via the `feature_tier` setting; cost + quality
views render a lock overlay for community users.

## Data layer notes

- **Ingestion buckets** come from a single Cypher query against
  Neo4j: `WITH substring(a.ingested_at, 0, 10) AS day RETURN day,
  count(*)`. No APOC required.
- **Cost telemetry** lives in Redis sorted sets (7-day TTL by
  default). Long-window queries beyond 7 days return only what's
  retained — extend `METRIC_RETENTION_SECONDS` if you want longer
  history.
- **Quality timeline** stitches together Redis time-series points
  bucketed to daily averages. The "latest" headline value is the
  most recent non-null per metric.

## Cross-link map

| From | To | Mechanism |
|---|---|---|
| Sunburst → Drill down | TrustScoreModal | Click button |
| Heatmap cell → Sources Activity | `?since=YYYY-MM-DD` URL param | Click cell |
| Sankey segment → (planned) | Cost breakdown by model | Future enhancement |
| Timeline metric → (planned) | Eval baseline file | Future enhancement |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Heatmap empty | No artifacts in Neo4j or `ingested_at` field missing | Trigger a manual ingest; verify `MATCH (a:Artifact) RETURN count(a)` returns > 0 |
| Sankey says "No LLM cost recorded" | Redis time-series flushed (7d TTL) | Wait for new LLM calls or extend retention |
| Timeline all-null | No `retrieval_ndcg` / `ragas_faithfulness` etc. recorded | Run the nightly eval pipeline once |
| Sunburst components shown as "not_available" | Eval baseline files missing | Run `make eval-baselines` or wait for nightly CI |
| Quality timeline shorter than 90 days | Data hasn't built up yet | Expected on a fresh install; component auto-trims leading nulls |

## Future work

- **Stage attribution at recording time** — augment
  `call_internal_llm` to record the stage tag alongside the model
  tag on every `llm_cost_usd` metric. Today's Sankey works via
  tag-when-present + "other" bucket; richer signal once that lands.
- **Sources Activity `?since=` receiver** — wire the heatmap's
  deep-link param into the activity stream's filter logic.
- **Per-component history for the sunburst inner ring** — backend
  emit historical values per component so the sunburst can show
  trend arrows; currently shows snapshot only.
- **Cost projection** — extrapolate the next-month spend from the
  30-day total. Useful headroom signal for Pro users on tight
  budgets.
