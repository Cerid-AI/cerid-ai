# Visualization Performance Budgets

Authoritative source for the FPS / latency / memory thresholds Atlas
(Phase A) and Constellation (Phase B) must meet on the reference
hardware. Regressions block release.

## Atlas (Phase A, 2D WebGL via sigma.js v3)

Measured 2026-05-21 on M2 Pro / macOS 15 / Chrome canary / dev build.
Median frame is the renderer-only wall-clock (`sigma.refresh()`); FPS
is the implied ceiling at unthrottled RAF. Real-world FPS will hit
display refresh rate (60Hz / 120Hz) or Chrome's macOS power-saver
30Hz throttle, whichever is the practical floor.

| Fixture | Edges | Layout | First frame | Median frame | p95 frame | Implied FPS |
|---|---|---|---|---|---|---|
| 100 nodes | 327 | 53ms | 114ms | ~2ms | ~3ms | 120+ |
| 1,000 nodes | 3,627 | 931ms | 1,049ms | **8.3ms** | 8.7ms | **120** |
| 1,000 + 4 lenses | 3,627 | — | — | **10.2ms** | 10.6ms | 98 |
| 5,000 nodes | 19,042 | 3,640ms | 3,876ms | 40.3ms | 46.5ms | 25 |
| 10,000 nodes | 36,207 | 5,360ms | 5,804ms | 101.3ms | 113ms | 10 |

### Phase A budgets (enforced)

| Fixture | Median frame | p95 frame | Layout | First frame |
|---|---|---|---|---|
| 100 | ≤ 4ms | ≤ 6ms | ≤ 200ms | ≤ 400ms |
| 1,000 | ≤ 12ms | ≤ 15ms | ≤ 1.5s | ≤ 1.5s |
| 1,000 + 4 lenses | ≤ 14ms | ≤ 17ms | — | — |

Above 1K nodes is **soft territory** for Phase A — the budgets exist
to track perf but a miss is not blocking until LOD or WebGL2
instancing lands:

| Fixture | Median frame | p95 frame | Layout |
|---|---|---|---|
| 5,000 | ≤ 50ms (target ≤ 25ms) | ≤ 60ms | ≤ 5s |
| 10,000 | ≤ 120ms (target ≤ 30ms) | ≤ 140ms | ≤ 10s |

### Why 5K+ is degraded

Three pressures stack at scale:

1. **Edge count grows non-linearly with N** under the power-law degree
   distribution — 5K nodes → ~19K edges (3.8 edges/node avg). Edge
   draw calls dominate frame budget at >2K edges.
2. **EdgeRectangleProgram is not instanced** — one draw call per edge.
   InstancedArrays (`ANGLE_instanced_arrays` extension) would slash
   edge cost ~10×.
3. **Labels** disable above 1K nodes (handled in harness + Atlas).
   Without LOD the screen becomes unreadable anyway above ~2K.

Path to 10K-node 60fps:

- Sigma v4-alpha ships WebGL2 + instanced rendering. Worth tracking
  upstream.
- LOD (level of detail): downsample to top-K mention_count entities
  at low zoom. Phase B Day 5+ territory.
- Custom edge program with InstancedArrays — feasible but ~2 weeks
  of work for a Phase A-only win.

## Constellation (Phase B, 3D R3F)

(Drafted; activated when Phase B Day 5 ships InstancedMesh + custom
shaders. R3F + drei InstancedMesh has been validated for 2K-node
60fps on Apple Silicon.)

| Fixture | Apple Silicon | Intel AMD |
|---|---|---|
| 1,000 nodes | ≥ 60fps, first paint ≤ 1.5s | ≥ 45fps, ≤ 2.5s |
| 2,000 nodes | ≥ 45fps | ≥ 30fps |

## Reference hardware

- **Primary**: M2 Pro MacBook Pro, 16GB unified memory, Sonoma
- **Stress**: Mac Pro 2019 (Intel Xeon W, AMD Vega II Duo, macOS 15)
  Vega II Metal correctness fixes track upstream via quenchforge.

## How to measure

Local manual:

```bash
cd src/web && npm run dev
# open http://localhost:5173/?dev=atlas-perf
```

Harness controls — pick N, click "Pan camera", toggle lenses. Two
readouts:
- **fps** — RAF-rate FPS (capped by Chrome / display)
- **render-cost** — `sigma.refresh()` wall-clock (the budget metric)
  with median + p95 + implied FPS

Automated:

```typescript
await page.goto("/?dev=atlas-perf")
await page.selectOption('[data-testid="node-count-select"]', "1000")
await page.waitForSelector('[data-testid="atlas-status"][data-status="ready"]')
await page.waitForTimeout(2000)
const median = await page.getAttribute('[data-testid="atlas-render-cost"]', "data-median-ms")
expect(Number(median)).toBeLessThanOrEqual(12)
```

CI gate lands in Day 13's preservation harness extension.

## Regression policy

A median frame regression of ≥30% on any fixture size blocks merge.
Tag PRs with measured numbers in the description. Nightly CI runs
the matrix at 100 / 1K / 5K / 10K, both with and without all 4 lenses.
