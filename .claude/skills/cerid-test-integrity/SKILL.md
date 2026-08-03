---
name: cerid-test-integrity
description: "Use when writing, reviewing, or trusting tests in Cerid AI repos (cerid-ai-internal, cerid-ai, cerid-trading-agent, cerid-boardroom, cerid-* repos) — and whenever a suite is green but behaviour is wrong, a gate 'passes' suspiciously fast, or you are about to claim something is covered. Encodes the repo's hardest-won lesson: a test that mocks the thing it tests proves nothing, and line coverage cannot tell the difference. Covers the mutation harness (make mutation-check), the vacuous-gate class, mock-drift, assert-intent-not-literals, the lint-test-antipatterns codes TA001-TA006, mock.patch's concurrency unsafety, and the red-green protocol required before calling a fix verified. For whether a NUMBER means anything (evals, quality gates, judges, metrics) use cerid-measurement-integrity instead; for where a result is valid (host vs container, cwd, ports) use cerid-environment-parity."
license: Apache-2.0
metadata:
  author: cerid-ai
  version: "1.0.0"
  scope: cerid-derivative-only
  applies-to:
    - "~/Develop/cerid-ai-internal/**"
    - "~/Develop/cerid-ai/**"
    - "~/Develop/cerid-*/**"
---

# Cerid Test Integrity

> **Scope:** Cerid AI repositories only. Out of scope elsewhere — the specific
> gates and file paths below are this codebase's.

## The one question

**What is left to fail?**

Ask it of every test you write or read. If you patch the function under test,
mock the module that decides the branch, or assert a literal you also supplied,
the answer is *nothing* — and the test still passes, still counts, and still
raises the coverage number.

This is not hypothetical here. A backup that silently discarded **100% of the
vector store** shipped with 8,000+ tests green, because the one test touching
`export_chroma` patched it out of existence. Coverage said "tested."

## Mutation testing is the arbiter

`make mutation-check` (`scripts/mutation_check.py`) injects real defects and
reports whether the suite notices. A **SURVIVED** mutant is a blind spot: the
code changed in a way that matters and everything still passed.

- Its first run scored **5/9** — all four survivors in `app/sync/export.py`, the
  module behind the worst defect of that day.
- It is now **22/22**, covering export, import/restore, auth, private mode,
  citations, claim promotion, and the graph link cap.

**When you fix a defect worth never re-shipping, add a mutant for it.** The
mutant is the proof your test detects the regression rather than merely
executing the line. Add the covering test to `TESTS` in the same edit, or the
mutant survives for lack of *selection* and you will misread it as a blind spot.

**Never run tests, a type-checker, or a build while the harness is running.** It
edits files in place; anything reading them sees deliberately corrupted source.
It takes an exclusive `flock` against a second harness, but it cannot stop a
concurrent reader. A failure observed during a mutation run is not evidence.

## The vacuous-gate class — assume nothing about a passing gate

A gate that cannot fail is worse than no gate: it reports safety.

- `npx tsc --noEmit` in `src/web` type-checks **zero files** — the root
  `tsconfig.json` is a Vite *solution* file (`"files": []` + `references`).
  `npx tsc --noEmit --listFiles | wc -l` → `0`. The frontend type gate is
  `tsc -b`. This shipped in the Makefile *and* in `.claude/hooks/typecheck.sh`,
  where it silently passed every edit until 2026-07-30.
- Piping a gate hides its exit status: `make prepush 2>&1 | tail -25` reports
  `tail`'s status. Two red pushes went out this way. Write to a file, or use
  `set -o pipefail`, and read the gate's own `Error N` line.
- A marker applied by *directory* measures the directory. `tests/integration/`
  auto-marks everything `preservation`; only ~66 of 239 resolve a live-stack
  fixture, so "254 live gates" overstated reality ~3.5x.

**Protocol:** when you rely on a gate, prove once that it can fail. Break
something deliberately and watch it go red. If it does not, you found a bug in
the gate, not a clean tree.

## Mechanically blocked shapes (`make drift-check`)

`scripts/lint-test-antipatterns.py` is a shrink-only per-file ratchet — new test
files start at baseline 0, so these are blocking for new code. Suppress a
genuinely-intended case with
`# lint-test-antipatterns: allow <CODE> — reason`.

| Code | Shape | Why it is vacuous |
|---|---|---|
| **TA001** | `asyncio.get_event_loop()` in a test | Passes alone; raises "no current event loop" once an earlier test closes it. Use `asyncio.run()`. |
| **TA002** | `importlib.reload()` of a re-export bridge (`config`, …) | Re-snapshots every `import *` source at its current state, laundering in-session global mutation into package attrs for all later tests. |
| **TA003** | `patch("mod.config")` | Replaces the module with a MagicMock, so every attribute you did not set is also a MagicMock. Comparisons raise, the code under test swallows it and stops running, and your envelope assertion still passes. Use `monkeypatch.setattr(mod.config, NAME, value)`. |
| **TA004** | `from mod import f` inside a block patching `"mod.f"` | The call resolves to the mock; the assertion checks the fixture you supplied. |
| **TA005** | calling the patch alias itself, then asserting on what it returned | You asserted on the value you just supplied. 15 of these sat in one file. |
| **TA006** | a patch whose target the test never reaches | An inert patch is invisible; the real code ran the whole time. |

**Two detector designs for TA006 were wrong before one worked, and both looked
reasonable.** The first fired 1,552 times — production reaches a patched symbol
*transitively*, so "the test doesn't import it" proves nothing. The second
reused a helper matching any `.patch(` attribute call, so `client.patch("/route")`
— a real TestClient request — read as mock plumbing and made an exercising test
look inert. Identify mock patches by callee name AND a dotted, slash-free
target. **When you write a detector, build a red/green probe covering the
shapes it must NOT flag**, not only the ones it must.

The ratchet does not catch judgment failures. The rest of this skill does.

## `mock.patch` is not concurrency-safe

Each entrant records the attribute's *current* value as the one to restore, so a
second coroutine entering while the first holds the patch records the
already-patched value and writes THAT back on exit.

Five concurrent `_run_query` calls under `asyncio.gather` leaked all five
feature flags out of `config.features`, and the damage surfaced three files away
as a `kb_batch` test resolving via `cross_model` — passing alone, failing in the
suite.

**Enter the patch ONCE around the `gather`, never inside the gathered
coroutine** (`test_simulated_sessions.py::_pinned_pipeline`). Bisect
order-dependent failures with a config-snapshot plugin rather than guessing.

## Mock drift — fakes encode a *past* reality

Three `_FakeBackend` clones disagreed on the same input; one hardcoded chromadb
**0.5** semantics while the server ran **1.5.9**. The fake was self-consistent
and wrong.

- One canonical fake per dependency: `tests/helpers/fake_chroma.py`,
  **version-pinned in a comment** to the version it mimics.
- `test_fake_backend_fidelity.py` is an AST guard that fails the build if a
  fourth clone appears.
- When you bump a dependency major, grep the *old* API across the whole tree —
  the Chroma 0.5→1.x migration moved `import_.py` and missed four other modules.
  One root cause, four shipped bugs.

**Patch the leaf dependency, never the unit under test.** If mocking is the only
way to reach a path, mock at the boundary (HTTP transport, driver, clock) and
let the real code run. `test_sync_export_chroma_wire.py` is the pattern: stub
`httpx` at the module edge, assert the URL actually requested.

**Check the patch target is real.** `test_verify_streaming_format` patched
`streaming.extract_claims` for months — a symbol `verify_response_streaming`
never calls. An inert patch is invisible; the real extractor ran the whole time.

## Assert intent, not literals

Two schedule tests pinned `"* * 0"  # Sunday`. APScheduler maps day-of-week `0`
to **Monday**, so both tests encoded the off-by-one as their expected value and
defended the bug. Assert the *resolved fire day*.

Corollary: a fixture that mirrors what the reader expects, rather than what the
emitter produces, tests your understanding instead of the system. Copy fixtures
from real payloads — `verified_memory.py` read a nested `sources[]` shape that
production has never emitted, and its unit tests agreed with it.

## Before claiming a fix is verified

1. **Red-green.** See the test fail without the fix. A test written after the
   fix, never observed red, is a hypothesis.
2. **Re-measure the symptom, not the diagnosis.** An audit finding bundles a
   reproducible symptom with an inferred cause. Tests written from the diagnosis
   pass whether or not the diagnosis is right — they share the assumption. The
   `/graph/map` orphan fix passed its tests while leaving all 583 false orphans
   in place; only re-running the original measurement caught it.
3. **Full suite, not targeted.** Any rename, signature change, or removal of a
   module-level name needs a full `make prepush` — `@patch("that.exact.path")`
   in unrelated files resolves at call time and only the importing test sees it.
4. **The last full run must postdate the last edit.**
5. **Re-run suspicious failures on a quiet machine.** Host contention under
   concurrent agents manufactures false failures (beta `i01`, memory extraction,
   frontend axe). Confirm before believing — and never "fix" one by raising a
   timeout.

## When coverage is the wrong question

Coverage counts a patched call site as covered, and counts unreachable modules
as tested. ~9 modules here are tested but unreachable in production, inflating
the number. Ask instead:

- Does any test execute this with the **real** collaborator?
- Would reverting the fix turn a test red? (If unsure: add a mutant.)
- Is the path reachable in the shipped product at all?
