# Environment Profiles and Hardware Expectations

This document is for operators deciding how to configure inference on their
hardware, and for anyone reading `/health/status` or `/setup/system-check`
output. It describes what the code in this branch actually does: hardware
classification, the boot-time throughput probe, the `CERID_ENVIRONMENT_PROFILE`
presets, and where the measured numbers surface in the product. Numbers below
are measured, not estimated, and the host they were measured on is named
every time.

Source of the numbers and the design rationale:
`tasks/2026-09-07-hardware-limited-client-configs.md`. Source of the
behavior described here: `src/mcp/config/environment_profiles.py`,
`src/mcp/config/settings.py`, `src/mcp/utils/inference_config.py`, and
`src/mcp/config/stage_profiles.py`. Where the two disagree, this document
describes the code and calls out the gap explicitly (see
"Where the code diverges from the design" at the end).

## 1. Hardware classes

`classify_hardware()` in `config/environment_profiles.py` puts every host
into one of two classes. There is no third hardware class in code — what the
design notes call "class C" is a *posture* (local-only), not a hardware
class; any host, A or B, ends up there when it has no usable cloud egress.

| Class | Examples | GPU / RAM test | What the local model realistically delivers |
|---|---|---|---|
| **A — CPU-bound local** | Mac Pro 2019 + Radeon Pro Vega II (AMD Metal is slower than CPU here), Intel Macs, Linux boxes without CUDA/ROCm, 8 GB laptops | anything that isn't class B | A 7B model at 5–10 tok/s generation; every enrichment/extraction call (memory extraction, entity extraction, wiki summary, claim extraction — note: `memory_extract` and `claim_extraction` are classified *interactive* in code, not background, despite running behind the scenes) costs 20–60 s, and the full chat-turn tail (memory extract + entity extraction together) measured 109.6 s on the reference host. See §2 for the measured reference numbers. |
| **B — GPU-accelerated local** | Apple Silicon with 16 GB+ unified memory, NVIDIA with 12 GB+ VRAM | `gpu_type` is `metal` or `nvidia` **and** `ram_gb >= 12` | A 7–8B model at 25–60 tok/s; local is the fast path for every stage, cloud is optional. |

`classify_hardware(host=None, gpu_type="", ram_gb=0)` accepts either a
`utils.host_info.HostHardware` snapshot or the two fields directly. An
unrecognized or undetected GPU type, or a fast GPU type without 12 GB of RAM,
is classified A — the function is deliberately pessimistic: an unknown host
gets the honest slow-path expectations rather than a flattering guess.

## 2. Measured reference numbers

**Offline benchmark harness** (§1 of the source analysis; identical harness
for all three rows — 1,014-token prompt, greedy decode, CPU only, 16 threads,
one request at a time, run on the reference Mac Pro 2019 / Xeon W-3245 /
Radeon Pro Vega II, class A):

| Model (Q4_K_M) | Prompt tok/s | Generation tok/s | 1,000-in / 200-out call |
|---|---|---|---|
| qwen2.5-7b | 100 | 9 | 31 s |
| qwen2.5-3b | 215 | 19 | 11 s |
| qwen2.5-1.5b | 390 | 34 | 8 s |

This table is the model-comparison reference: it is how the branch decided a
3B model belongs in the background slot (§6 below) — 2.5× faster than the 7B
at equal extraction recall on a 10-entity JSON probe.

**Live throughput probe** (Task 1's `probe_local_throughput`, measured
against the same reference Mac Pro 2019 / Xeon W-3245 / Radeon Pro Vega II,
7B Q4_K_M served by quenchforge on CPU, under normal production conditions
rather than the isolated benchmark harness above): **prompt 88 tok/s,
generation 8.7 tok/s**. Close to the offline benchmark's 100/9 for the same
model — the small gap is expected between an isolated single-shot benchmark
and a live gateway call. Projected from those two rates through the stage
token shapes in §3:

| Function | Projected latency |
|---|---|
| Memory extraction | 45.7 s |
| Entity extraction | 63.9 s |
| Chat-turn background tail (memory extract + entity extraction) | 109.6 s |

These are the numbers `/health/status.inference.expectations` and
`/setup/system-check.local_throughput` report on this host once the probe has
run once successfully. On a different host they will be different — that is
the point of measuring instead of assuming.

**Cloud reference** (OpenRouter, `gpt-4o-mini` class): 1–3 s per call, about
$0.0003 per 1,500-token call. This is the number the `hybrid` and
`cloud-first` profiles are trading for when they route a stage to the cloud.

## 3. The boot-time throughput probe

`utils/inference_config.py::probe_local_throughput()` measures real local
chat-model throughput with one small completion — it does not infer a rate
from hardware detection or a benchmark table.

- **When it runs:** once as a fire-and-forget task at `main.py` lifespan
  startup, and again every pass of `_inference_recheck_loop()`
  (`INFERENCE_RECHECK_INTERVAL`, default 300 s). Always off the request
  path — no user request waits on it.
- **When it's skipped:** entirely, when `INTERNAL_LLM_PROVIDER` isn't a
  local provider (`ollama` or `quenchforge`).
- **What it sends:** a 256-token prompt (a UUID nonce followed by
  repeated filler, so a prompt-prefix cache never turns the probe into a
  no-op — an earlier fixed-string probe collapsed to `prompt_n: 1` against
  quenchforge's cache) and asks for 64 generated tokens, greedy
  (`temperature: 0`).
- **How it reads timing:** quenchforge is called via its OpenAI-compatible
  `/v1/chat/completions` route (its native `/completion` route isn't
  exposed) and its response carries llama-server's `timings` block
  (`prompt_per_second`, `predicted_per_second`) alongside the OpenAI
  `usage` shape. Stock Ollama is called via `/api/generate` and its rate is
  derived from `prompt_eval_count`/`prompt_eval_duration` and
  `eval_count`/`eval_duration`. If neither shape reports per-phase timing,
  the probe falls back to a coarse whole-round-trip rate rather than
  discarding the completion.
- **Cache-hit guard:** if quenchforge reports it processed fewer than half
  the prompt tokens actually sent, the result is discarded — a cache hit
  measured almost nothing and storing it would look like "measured" data.
- **Concurrency:** the probe acquires the same pacing-gate slot as any other
  `BACKGROUND`-class caller (`INTERNAL_LLM_MAX_CONCURRENCY`) — it queues
  like every other background stage, never rides as an extra permit or
  displaces an interactive caller.
- **Timeout:** `LOCAL_THROUGHPUT_PROBE_TIMEOUT_S` (`config/constants.py`,
  90 s). On timeout or any HTTP/parse failure, the rate fields are left as
  they were — `None` until a probe actually succeeds, never a stale or
  guessed value — and one INFO line explains why.
- **What it stores:** `InferenceConfig.local_prompt_tok_s`,
  `local_gen_tok_s`, `local_probe_at` — module-level singleton state, read
  by `expectations_for()`.

`expectations_for(cfg)` is a pure function: it never triggers a probe, it
only projects `STAGE_TOKEN_SHAPES` through whatever rate is currently
recorded. The shapes (from the source analysis §1/§4a, fixed — only the
tok/s rate is measured):

| Stage | Prompt tokens | Output tokens |
|---|---|---|
| `memory_extract` | 1,000 | 300 |
| `entity_extraction` | 1,600 | 400 |
| `wiki_summary` | 900 | 500 |
| `claim_extraction` | 800 | 200 |
| `topic_extraction` | 700 | 100 |

Each stage reports `{"seconds": <projected>, "basis": "measured"}` once a
probe has succeeded, or `{"basis": "unmeasured"}` before the first probe
completes (e.g. a fresh boot, or a local provider that has never answered).
`chat_turn_tail_s` sums `memory_extract` + `entity_extraction` — the two
stages every chat turn pays for synchronously.

**Where this surfaces:**

- `/health/status.inference.expectations` (`utils.inference_config.
  inference_health_payload`).
- `/setup/system-check.local_throughput` — `{prompt_tok_s, gen_tok_s,
  probe_at, expectations}`, plus `suggested_profile` and `active_profile`
  (see §4).
- Setup wizard, local-LLM step (`local-llm-step.tsx`): once
  `isLocalThroughputMeasured()` is true, the CPU-only guess is replaced with
  a measured line — "Local model measured at *N* tok/s — background
  enrichment ~*N*s per document, memory extraction *N*s per chat turn.
  Suggested profile: *X* (*reason*)."
- Settings → System (`components/settings/categories/system.tsx`): the same
  three numbers, plus "Active: *X* · Suggested: *Y*" reading
  `active_profile` / `suggested_profile` from `/setup/system-check`.

## 4. Environment profiles

`CERID_ENVIRONMENT_PROFILE` is one of `cloud-first`, `hybrid`, `local-only`,
or empty (no preset). There is no fourth value and no separate "local-first"
profile in code — see §7 for where the source analysis's prose diverges from
this.

A profile is **defaults only**: `config/settings.py::
_apply_environment_profile_defaults()` runs at import time, computes the
profile's knob values, and applies them with `os.environ.setdefault(...)` —
before every other `os.getenv(...)` read further down the file. An operator
value already present in the environment (from `.env`, the shell, or the
container's env vars) always wins; the profile only fills in what is unset.
This includes the profile-derived `INTERNAL_LLM_PROVIDER` default (§6) —
that default is written to the **process environment only**, never to
`.env`, so it disappears the moment the profile is unset or the process
restarts with a different profile.

### Suggestion vs. setting

- **Suggested**: `config.environment_profiles.suggest_profile(hardware_class,
  has_cloud_key, private_mode_level)` — pure, no state written. Returns
  `local-only` whenever Private Mode is L1+ or no cloud key is configured;
  otherwise `local-only` for class B (already fast locally, nothing to gain
  from the cloud) and `hybrid` for class A (a 20–60 s local wait becomes a
  1–3 s cloud call). Computed live on every `/setup/system-check` call and
  surfaced as `suggested_profile` — it is a recommendation, never applied.
- **Set**: the operator (or the setup wizard, writing on the operator's
  behalf) sets `CERID_ENVIRONMENT_PROFILE` in `.env`. That value is read once
  at process boot as `config.CERID_ENVIRONMENT_PROFILE` and resolved against
  the *boot-time* Private Mode posture (`CERID_PRIVATE_MODE` /
  `CERID_PRIVATE_MODE_LEVEL` env pair — Private Mode's live level lives in
  Redis and isn't knowable at import). `/setup/system-check.active_profile`
  recomputes the resolution against the *live* Private Mode level on every
  call, so a Private Mode change during the session is reflected there even
  though the process's own applied defaults don't re-apply until restart.

### Degrade rule

`resolve_profile(requested, hardware_class, has_cloud_key,
private_mode_level)`: `local-only` is requested as-is (there's nothing to
degrade to). `cloud-first` and `hybrid` degrade to `local-only` when either
of these is true:

- no `OPENROUTER_API_KEY` is configured, or
- Private Mode is L1 or higher (`private_mode_level >= 1`).

`apply_environment_profile()` is the single place a degrade is announced:
exactly one `logger.warning(...)` naming the requested profile, what it
degraded to, and the specific reason(s) (joined with "and" when both apply).
An unrecognized `CERID_ENVIRONMENT_PROFILE` value degrades to no preset
(empty string) with its own warning rather than silently falling through to
a default profile.

## 5. Per-profile routing table

Everything below is `config.environment_profiles.profile_defaults()` —
applied as `os.environ.setdefault` defaults, so any of these an operator has
pinned already keeps its pinned value.

| Knob | `cloud-first` | `hybrid` | `local-only` |
|---|---|---|---|
| Interactive stages (`INTERACTIVE_STAGES` \| `MCP_STAGES`) | cloud, cheap tier where hardness fits | cloud, cheap tier where hardness fits | local (no cloud route added — global provider applies) |
| Background stages (`BACKGROUND_STAGES`) | cloud, cheap tier where hardness fits | local (no cloud route added) | local |
| `INTERNAL_LLM_PROVIDER` | left alone (every named stage already routes to cloud) | defaulted to the detected local backend | defaulted to the detected local backend |
| `INTERNAL_LLM_MAX_CONCURRENCY` | 2 | 2 | 1 on class A, 2 on class B |
| `VERIFY_CLAIM_MAX_CONCURRENT` | 3 | 3 | 2 |
| `WIKI_REFRESH_LIVE_MAX_PER_HOUR` | 12 | 12 | 4 |
| `COMMUNITY_SUMMARY_WALL_CLOCK_S` | 480 | 480 | 240 |

Notes on the stage routing:

- "Cheap tier where hardness fits" means `PROVIDER_STAGE_<STAGE>=openrouter`
  for every stage in the set, and `PROVIDER_STAGE_<STAGE>_MODEL=<cheap tier
  model id>` only for stages whose `Hardness` is `TRIVIAL`, `SIMPLE`, or
  `MODERATE` (`_accepts_cheap_tier`). A `HARD` or `FRONTIER` stage, and any
  of the five eval stages (`faithfulness/decompose`, `faithfulness/score`,
  `context_precision`, `context_recall`, `answer_relevancy`), keeps whatever
  model the tier registry already assigns it under every profile — no
  profile ever re-points a judge or a frontier-generation stage to the cheap
  tier. This is deliberate: swapping the model underneath a RAGAS judge
  would make every score before and after the switch incomparable, silently.
- A stage that is background but not enumerated in `STAGE_PROFILES`
  (`topic_extraction`, `session_summary`, `entity_merge_adjudication`) gets
  no per-stage cloud route from `cloud-first` either — it isn't in
  `_ROUTABLE_INTERACTIVE_STAGES` or the cheap-tier hardness map by
  construction, and follows whatever `PIPELINE_PROVIDERS`/global default
  applies. A runtime `mcp_*` stage not in `STAGE_PROFILES` gets no per-stage
  route from any profile, but is still classified interactive by the pacing
  gate (which matches by the `mcp_` prefix, not by enumeration).
- "Verification cross-model pool" — the design table describes this as
  "cloud" under `cloud-first`/`hybrid` and "KB grounding only, web claims
  reported as unverifiable offline" under `local-only`. **No profile sets
  this today** — see §7.

## 6. The background model slot

`INTERNAL_LLM_MODEL_BACKGROUND` (empty by default) names a second, smaller
local model — a 3B-class tag — dedicated to `config.stage_profiles.
BACKGROUND_STAGES`. `hybrid` and `local-only` both default
`INTERNAL_LLM_PROVIDER` to the detected local backend so this slot is
actually reachable (the shipped global default is `openrouter`, under which
`INTERNAL_LLM_MODEL_BACKGROUND` would never be consulted).

Resolution (`core.utils.internal_llm._local_model_for_stage`): for a
background stage, if `INTERNAL_LLM_MODEL_BACKGROUND` is set **and** the
gateway's currently-served model list actually contains that name, calls for
that stage use it; otherwise they fall back to the same model everything
else uses. The served-list check exists because an unserved model name 400s
on some builds rather than routing away cleanly — an unconfigured or
not-yet-loaded second slot must degrade to the chat model, not fail every
background call. One INFO line logs the resolution, and only when it
*changes* (not per call — the served list is re-checked on a TTL, so logging
every hit would repeat the same fact roughly 288 times a day).

On class-A hardware this is the single biggest lever in this branch: routing
`entity_extraction`/`wiki_summary`/`topic_extraction` to a 3B instead of the
7B chat slot is ~2.5× faster at equal extraction recall (§2), without touching
the interactive chat model at all. `memory_extract` and `claim_extraction`
are interactive stages (§1) and never use the background slot; on `hybrid`
they go to the cloud cheap tier instead.

### Serving the second slot with quenchforge

quenchforge `main` after PR #24 (2026-09-07, not yet in a tagged release) supervises an optional
second chat-class slot. Set
`QUENCHFORGE_BACKGROUND_MODEL=<gguf name under the models dir>` (and optionally
`QUENCHFORGE_BACKGROUND_PORT`, default 11507, and `QUENCHFORGE_PLACE_BACKGROUND`, which follows
the chat slot's CPU/GPU rule) in the service's environment and restart it once. Chat requests
whose `model` names that GGUF route to the slot; every other name keeps going to the chat slot;
a request for the background model while its slot is down gets 503 rather than a silent answer
from the other model. Then set `INTERNAL_LLM_MODEL_BACKGROUND` to the same name here. Note that
quenchforge's `/api/tags` lists every cached GGUF, not only loaded ones, so cerid's served-model
check passes as soon as the file exists; if the slot is not configured the gateway answers with
the chat model.

### What a 3B does differently

Measured on the reference host with the production prompt (`tasks/2026-09-07-3b-extraction-eval.md`):
qwen2.5-3b runs a fixture in 13.3 s against the 7B's 20.5 s and finds a similar set of names,
but it omits the `confidence` field for 90% of entities, emits bare quantities and heading
fragments as entities at four times the 7B's rate, and returns malformed JSON on about one
fixture in six. The extractor handles all three (a missing confidence is kept at the threshold,
bare quantities and heading leaks are rejected, a malformed reply is retried once), and it logs
`entity_extraction.confidence_unreported`, `entity_extraction.json_retry` and
`entity_extraction.skipped` so a silent zero-entity artifact cannot recur. Expect somewhat fewer
entities per document than the 7B produces.

## 7. Where the code diverges from the design

The source analysis (`tasks/2026-09-07-hardware-limited-client-configs.md`
§4b) is the design intent; this section notes the two places the shipped
code does less than that table implies. Code wins in both cases — the table
above and this document describe what actually runs.

1. **No separate "local-first" profile for class B.** The design's class-B
   posture is called "Local-first" and described as "an alias of hybrid with
   local interactive stages." In code, `VALID_PROFILES` has exactly three
   members (`cloud-first`, `hybrid`, `local-only`) and `suggest_profile()`
   returns `local-only` outright for class B — there is no alias, and no
   fourth profile value exists anywhere. The practical effect is close (class
   B already has a fast local path, so routing everything local is the same
   trade the alias describes), but an operator or log line will never see
   "local-first" — only `local-only`.

2. **`local-only` does not by itself turn off cross-model or web-search
   verification.** `profile_defaults()` sets only the four numeric knobs and
   the stage-provider routing in §5 — it does not touch
   `ALLOW_CLOUD_EGRESS_WHEN_LOCAL`, which defaults to `true` and is read
   independently by `core.agents.hallucination.verification.
   _verify_claim_externally` (and by the internal-LLM fallback chain in
   `core/utils/internal_llm.py`). Cross-model and web verification calls are
   hard-wired to OpenRouter regardless of `INTERNAL_LLM_PROVIDER`; the only
   gate that stops them is `ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false` combined
   with a local provider being active. Private Mode's levels (0–4,
   `app/services/private_mode.py`) gate saves/sync/audit/session-wipe — none
   of them gate outbound LLM calls either.

   In practice this means: setting `CERID_ENVIRONMENT_PROFILE=local-only`
   alone still lets verification egress to the cloud if `OPENROUTER_API_KEY`
   is present and `ALLOW_CLOUD_EGRESS_WHEN_LOCAL` is left at its default
   `true`. An operator who wants what §8 below describes — no web-verified
   claims, no cross-model verification — needs `local-only` **and**
   `ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false` set together. (Private Mode L1+
   already forces the *profile* to degrade to `local-only` per §4, but does
   not independently set this flag.)

## 8. What `local-only` gives up

With the profile alone: a slower interactive path (chat and MCP-tool stages
run on the local model instead of the cloud's 1–3 s round trip) and lower
background throughput ceilings (§5's concurrency and refresh caps), in
exchange for zero outbound token traffic from every named pipeline stage.

With `local-only` **and** `ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false` (see §7):
additionally, no cross-model verification pool and no web-search
verification for claims — `_verify_claim_externally` returns an "uncertain"
verdict with `verification_method: "none"` instead of calling out, so claims
that would otherwise be checked against a second model or fresh web results
are reported as unverifiable rather than silently unchecked. KB-grounded
verification (matching a claim against retrieved knowledge-base content)
is unaffected either way — it never leaves the box.
