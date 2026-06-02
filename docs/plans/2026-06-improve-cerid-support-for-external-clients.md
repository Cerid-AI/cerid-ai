# Plan: Improve cerid-ai to Better Support External Clients (e.g. cerid-boardroom and future)

**Date**: 2026-06  
**Context**: Lessons from cerid-boardroom integration (personal 8888 Cerid as backend for executive agent team). Clients must currently apply shims, workarounds, and result-checking because the server/SDK assume "built-in" taxonomy, limited LLM task_types, and incomplete ingest surface. Goal: make custom domains, rich metadata, flexible LLM task_types, and client contribution first-class so clients need fewer hacks and get better retrieval/observability. This benefits any external system (other agents, tools, GTM systems, etc.) using Cerid as KB + LLM + memory backend.

**Success Criteria**:
- A client can `ingest(..., domain="my_custom", metadata={rich_provenance})` and have metadata stored and queryable.
- `query(domains=["my_custom", "general"])` succeeds and returns content from custom without 400 or heavy fallback to external.
- `llm.complete(..., task_type="my_creative" or "boardroom_w4")` succeeds (maps unknown to safe "internal" behavior) and can influence routing.
- Official SDK supports the main surfaces used by sophisticated clients (ingest with metadata, collections(), health_detailed(), plugins(), memory extract with targeting options).
- Health/settings expose client-relevant capabilities.
- No regression for built-in personal/general flows.
- Docs + examples for "using Cerid as client backend".
- (Stretch) Custom domain content participates well in agentic /query and ranking.

**Non-Goals** (for this plan):
- Full multi-tenant isolation or per-client redis (separate).
- Changing the core TAXONOMY for built-ins.
- UI changes.

## P0 / Foundational (unblock basic client use without hacks)

1. **Relax domain validation for custom/client domains (core blocker for 400s)**  
   - Files: `src/mcp/core/agents/query_agent.py` (multi_domain_query), `src/mcp/app/agents/decomposer.py` (similar invalid_domains check), `src/mcp/app/mcp_tools/retrieval.py`, `src/mcp/app/mcp_tools/*.py` (various domain checks), `src/mcp/app/routers/*.py` (kb_admin, artifacts, taxonomy).  
   - Change: Remove or soften the hard `if d not in DOMAINS: raise ValueError("Invalid domains...")`.  
     - For query paths: proceed for any domain; only warn/log if not in built-in DOMAINS. Rely on pre-check of existing_collections from chroma (already there). If collection doesn't exist yet, return [] gracefully instead of error.  
     - Update `if domains is None: domains = DOMAINS` to perhaps also include known chroma collections, or keep as built-ins + let callers specify customs.  
     - In error messages / tool descriptions: change from hard "Valid: {DOMAINS}" to "Built-in domains: {...}. Custom domains you have ingested to are also supported."  
   - Why: Clients like boardroom use `boardroom_foundation`, `cerid_ai_business_pilot` etc. for isolation of their outputs/patterns. Enforcing static list forces client shims or breakage. Ingest already allows arbitrary via `collection_name` (any slug).  
   - Tests: Add in `src/mcp/tests/` for query with unknown-but-ingested domain; update existing invalid test expectations.  
   - Also update: `src/mcp/app/main.py` pre-warm comment (note customs are lazy). `src/mcp/config/taxonomy.py` docs for CERID_CUSTOM_DOMAINS (mention clients can just use any domain name on ingest; the env is for built-in-like taxonomy entries with descriptions/icons).

2. **Support rich metadata on SDK /ingest (and server router)**  
   - Files:  
     - Server: `src/mcp/app/routers/sdk.py` (sdk_ingest: change `metadata={"tags": req.get("tags","")}` to `metadata = req.get("metadata") or {"tags": req.get("tags","")}` ; pass to `ingest_content`). Do same for other ingest entrypoints if they hardcode.  
     - SDK python: `packages/sdk/python/src/cerid/resources/kb.py` (add `metadata: dict | None = None` to `ingest`, include in `_build_json` call — the _base already drops Nones). Update docstring.  
     - SDK typescript: equivalent in `packages/sdk/typescript/src/client.ts` or resources (add metadata support to ingest).  
     - Client example / docs: `docs/SDK_GUIDE.md` (add section "Rich metadata and provenance for client artifacts", example with boardroom-style provenance).  
     - OpenAPI: run `scripts/gen_sdk_openapi.py` after, or update `docs/openapi-sdk-v1.json`.  
   - Why: Boardroom `produce_boardroom_memory` and pack ingest pass rich `metadata={"title":, "provenance": "boardroom_foundation_knowledge_pack", "source_file":, ...}` for attribution, filtering, closed-loop. Currently dropped → clients lose provenance on the server side. Server core `ingest_content` already accepts and stores metadata (used in neo4j, chroma, dedup).  
   - Also: Ensure `ingest_structured` / external paths support it. Update any tests that assert on ingest metadata.

3. **Make LLM task_type flexible for client-defined values (avoid 500/enum errors)**  
   - Files: `src/mcp/core/utils/llm_client.py` (route_and_call: wrap `TaskType(task_type)` in try/except ValueError: task = TaskType.INTERNAL; log "unknown task_type %s from client, defaulting to internal" ), `src/mcp/core/routing/smart_router.py` (TaskType enum stays, but route() can accept str and map unknown to INTERNAL; update the if task_type in (INTERNAL, CLASSIFICATION) to also treat unknown client "creative"/"marketing"/"gtm" etc as internal-like for ollama preference).  
   - SDK / models: `src/mcp/app/models/sdk.py` (SDKLLMCompleteRequest.task_type: keep str, expand description to "built-in: chat|internal|... ; clients may use custom values (e.g. 'gtm_creative', 'boardroom_w4') which default to internal behavior)").  
   - Router: `src/mcp/app/routers/sdk.py` (pass through).  
   - Why: Clients have domain-specific task types for their agents (creative, marketing, routing, w4 phases). Current enum + TaskType() raises or routes poorly (to chat which avoids ollama). Forcing clients to "internal" works but loses future opportunity for custom routing (e.g. "gtm" -> prefer certain cheap model). Map unknown -> internal for now (ollama first for ops-like).  
   - Future: Allow config for task_type -> preferred tier/model hints.  
   - Tests: Add cases for custom task_type in llm tests; ensure it gets ollama when available.

4. **Robust handling of null/None response_format and slo_budget_ms**  
   - Files: `src/mcp/app/models/sdk.py` (review Field for slo: the `ge=100` may interact badly with explicit null; consider `Field(default=None, ...)` and validator that skips constraint if None).  
   - `src/mcp/app/routers/sdk.py` and `llm_client.py`: ensure `if req.slo... is not None` and same for response_format before passing; the current `if response_format:` in _call_ paths is good but make explicit.  
   - SDK clients: already do `if is not None` in _body — keep/enforce.  
   - Why: Clients (and wrappers) sometimes pass the keys with null (json serialization, Optional in their models). Caused 500s or validation in the boardroom integration until they filtered client-side. Server should be tolerant (treat null/omitted the same: no constraint, omit from payload to ollama/openrouter).  
   - Add to health or settings: example of full request with/without.

## P1 -- Better Retrieval, Contribution, and Observability for Clients

5. **First-class support for custom domains in agentic retrieval and ranking**  
   - Files: `src/mcp/core/agents/query_agent.py` (multi_domain_query: after allowing the domains, in query_domain and aggregation, treat custom the same as built-ins for hybrid vector+bm25. No special-casing that assumes only DOMAINS).  
   - `src/mcp/core/agents/decomposer.py` and related planning: allow custom in sub-queries/domains lists.  
   - Retrieval core (bm25, vector, hybrid): ensure they work for any col_name = collection_name(custom).  
   - Ranking/affinity: Review if there's bias to built-in DOMAINS or "general". Consider a small boost when the domain was explicitly requested by the caller (clients often want their private context first). Log when custom domains contribute.  
   - Health/invariants: `src/mcp/app/routers/health.py` or invariants code: only report collections_empty for built-in DOMAINS (already the case); add a separate "custom_collections" or "client_domains" count/summary so operators see client activity without false "empty invariant" alerts.  
   - Why: Custom domains were falling back to general/external or low confidence. For testing packs and client memory (boardroom_foundation, pilot logs), clients need reliable retrieval when they explicitly name the domain. Improves closed-loop (client writes GTM outputs → later queries find them).

6. **Allow targeting custom domains from memory extract / client contributions**  
   - Files: `src/mcp/core/agents/memory.py` (extract_and_store_memories and _core: add optional `target_domain: str = "conversations"` param; use it in the ingest_content call instead of hardcode "conversations"; still use conversations for the built-in chat memory feature).  
   - MCP / SDK surfaces for memory: expose the option in the extract request if it makes sense (or document "for client artifacts, prefer the regular /ingest with your domain + metadata").  
   - Boardroom-style: once ingest metadata works, clients can use `ingest(..., domain=their_foundation, metadata=provenance)` directly for GTM/W4 outputs. Enhance memory extract to be a convenience that also supports `domain=`.  
   - Why: Currently memories go to conversations domain. Clients want to namespace their produced plans, decisions, GTM artifacts into their own collections for later retrieval (e.g. "what campaigns did we run?").

7. **Expand official SDK to cover common client surfaces (reduce need for custom adapters)**  
   - Files: `packages/sdk/python/src/cerid/resources/` (add or enhance: kb.collections(), system.health(), system.health_detailed(), system.plugins(), memory.extract (and async variants), kb.search with domain, etc. Mirror the CeridClient Protocol surfaces from boardroom clients).  
   - Update `_async_client.py` / client.py to expose `client.collections()`, `client.health_detailed()`, `client.plugins()`, `client.memory` resource with extract.  
   - Typescript SDK: parallel updates.  
   - Why: Boardroom implemented OfficialCeridAdapter + BoardroomCeridClient fallback because SDK was missing query/collections/health_detailed/plugins/memory_extract surfaces (or async polling). Making official SDK complete means clients (boardroom, trading, future) can `from cerid import AsyncCeridClient; c = ...; await c.kb.ingest(..., metadata=...) ; await c.llm.complete(..., task_type="my_type")` without wrappers or "Phase 0 adapter" hacks.  
   - Also implement any missing that are cheap (list_mcp_tools? if backend supports).

8. **Client-aware diagnostics in health, settings, and taxonomy**  
   - Files: `src/mcp/app/routers/health.py` (add to base health or detailed: "client_support": {"custom_domains": true, "ingest_metadata": true, "flexible_llm_task_types": true, "example_custom_domain": "boardroom_foundation"}), "active_custom_collections": count or sample of non-built-in collections.  
   - `src/mcp/app/routers/sdk.py` (sdk_settings or /taxonomy: note "DOMAINS are built-ins; custom domains supported on ingest/query").  
   - `src/mcp/app/main.py` or health invariants: don't treat unknown client collections as invariant violations.  
   - Why: Helps clients and operators discover capabilities without trial-and-error (the source of many "connectivity" surprises). Exposes what the server actually supports for external use.

## P2 -- Polish, Docs, Examples, and Extensibility

9. **Docs and examples for client usage**  
   - `docs/SDK_GUIDE.md`: New section "Using Cerid as a Backend for External Agents / Clients" — ingest to custom domains, rich metadata for provenance, custom task_types, retrieving your context, memory for your outputs, closed-loop via webhooks or record_outcome. Example snippets for python SDK (and curl). Reference the boardroom integration patterns.  
   - `docs/ENV_CONVENTIONS.md`: Expand CERID_CUSTOM_DOMAINS note (optional for "built-in-like" clients; any domain name works for ad-hoc client use).  
   - `docs/API_REFERENCE.md` or openapi: document that domains in ingest/query can be arbitrary client strings.  
   - Add a minimal "external-client" example in tests/beta or docs (a tiny script that ingests to "my_gtm" and queries it).  
   - Update `docs/KNOWLEDGE_PACKS_CATALOG.md` or similar to mention client-contributed packs live in their domains.

10. **Improve ranking/affinity for explicitly requested custom domains**  
    - In hybrid retrieval, bm25, vector, or the agentic query aggregator: when domains= list includes customs, boost or prioritize results from the explicitly named domains (clients know what they want).  
    - Add a note in query docs: "For your private client data, list the domain explicitly in domains=[...] for best results."  
    - Optional: small server-side config or per-call flag "boost_explicit_domains".

11. **SDK / server niceties for clients**  
    - In ingest response and memory, surface the actual domain/collection used (already does in some paths).  
    - Support `collection` alias or explicit in some calls if clients prefer collection names.  
    - In the python SDK, add convenience like `client.ingest_to_domain(domain, content, metadata=...)`.  
    - Update the CeridClient Protocol (if there's a shared one) or document the minimal surface for a "good citizen client": ingest with metadata, query with your domains, llm with your task_types, health checks, memory extract for your context.

12. **Tests, compatibility, and migration**  
    - Add integration tests (in `src/mcp/tests/` and beta/) that simulate a client: ingest to custom domain with metadata, query it mixed with general, llm.complete with custom task_type, verify metadata roundtrips and retrieval.  
    - Test the relaxations don't break built-in flows or invariants.  
    - Update any hard-coded "Valid domains" lists in tests/docs.  
    - Run the SDK openapi gen and client regen after changes.  
    - Consider a small "client_compatibility" test matrix (different task_types, with/without metadata, custom + built-in domains).

13. **Future / nice-to-have (after basics)**  
    - Per-client or namespaced custom domains with descriptions (extend the CUSTOM_DOMAINS env or add a lightweight registration).  
    - Client-specific routing hints for task_types (config map "my_creative" -> prefer cheap tier).  
    - Webhook / feedback surfaces tailored for clients (e.g. "record_gtm_outcome" that tags to the client's domain).  
    - Better support in the query decomposer/planner for client domains (auto-include "general" + client's known ones?).  
    - Observability: attribute costs / latency / retrieval quality back to "client: boardroom" or "source: external_agent".  
    - Make the audit store (Redis) configurable per-namespace or optional for pure client workloads.

## Cross-Cutting and Rollout
- **Order**: Do P0 (domain validation, metadata on ingest/SDK, flexible task_type, null robustness) first — these directly eliminate the shims/workarounds seen in boardroom.
- **Testing**: Use the cerid-boardroom test domain + the live orchestrator/Growth smokes as regression. Add client-sim tests in cerid-ai.
- **SDK publishing**: After python/ts changes, bump versions, update SDK_GUIDE and changelog.
- **Docs/Communication**: Add to ROADMAP under P1/P2 "External Client & Agent Backend Improvements". Note in CHANGELOG.
- **Migration for existing clients**: Mostly additive (old behavior for built-ins preserved). Clients can remove shims gradually.
- **Related files to touch (summary)**: taxonomy.py, query_agent.py, decomposer.py, sdk.py (router + models), llm_client.py, smart_router.py, ingestion.py (minor), kb.py (SDK), _base.py (minor), health.py, main.py (prewarm/docs), SDK ts equivalent, various mcp_tools/*.py (docstrings), docs/SDK_GUIDE.md, docs/plans/..., tests/.

**Why this makes cerid-ai a better platform for clients**:
- Clients get isolation (their domains) + rich attribution (metadata) + flexible LLM usage without fighting the server.
- Retrieval and contribution become reliable for their data → better closed-loop, better agent reasoning when using the pack + their own outputs.
- Official SDK becomes the single way to talk to Cerid, reducing custom adapters and "Phase 0" compatibility layers.
- Operators see client activity cleanly in health without invariant noise.
- Reduces the "connectivity debugging" tax observed when bringing up boardroom (and will for future clients like trading extensions, other vertical agents, etc.).

Pick items from P0 first for the next focused session. The boardroom integration can serve as the canary (re-run their smokes/harness after changes, remove temporary shims where possible).

This plan is derived directly from the concrete errors, workarounds, and verification steps in the cerid-boardroom + cerid-ai integration.
