# MCP tool style guide

Canonical conventions for tools surfaced by `cerid-kb` over MCP. Applies
to every entry in `app/tools.py::MCP_TOOLS`, every `@register_tool`
decorator in `app/mcp_tools/*.py`, and any internal-build-only tool
modules registered through the same hooks.

Two layers enforce this guide:

* **`tests/test_mcp_tool_schema_fidelity.py`** — structural invariants
  (schema shape, type, required fields).
* **`scripts/lint-mcp-descriptions.py`** — prose anchors (`Use when` /
  `Returns`). Warn-only in CI through v0.95.x; promoted to blocking
  in v0.96.

If a tool can't satisfy the guide for a legitimate reason
(e.g. an opaque-by-design ping), add it to the `_ALLOWLIST` in the
linter with an inline justification.

## Description anchors

Every description must include:

1. **Action verb-phrase** as the first sentence ("Fetch one artifact",
   "Score artifact quality", "Run Louvain community detection").
2. **`**Use when**`** — the triggering situation. Tells the LLM why to
   reach for *this* tool over a similar-named one. Resolves the
   `pkb_query`/`pkb_agent_query` overlap, the
   `pkb_rectify`/`pkb_maintain`/`pkb_audit` overlap, the four ingest
   variants, etc.
3. **`**Returns**`** — sketch of the result shape. Lets the LLM plan
   tool chains without paging through `outputSchema`.
4. (Optional) **Caveats** — cost class, side effects, error codes the
   tool might raise.

## Canonical template

```
{action_phrase}. **Use when** {triggering situation}. **Returns**
{result-summary}. {optional caveats / cost notes / -32004-class errors}
```

## Examples — before / after

### `pkb_artifacts`

**Before:**

> List ingested artifacts in the knowledge base, optionally filtered
> by domain

**After:**

> List ingested artifacts in the KB. **Use when** inventorying recent
> ingests, looking up an artifact ID for `pkb_artifact_get` /
> `pkb_recategorize`, or auditing what landed in a domain. **Returns**
> `{artifacts: [{artifact_id, domain, source, chunks, created_at}]}`.
> Default limit 50.

### `pkb_recategorize`

**Before:**

> Move an artifact from one domain to another in the knowledge base

**After:**

> Move one artifact (and its chunks) from one domain to another.
> **Use when** an ingest landed in the wrong domain (e.g. a project
> doc tagged 'general'). Atomic at the Neo4j level; ChromaDB chunks
> are migrated between collections. **Returns** `{status, artifact_id,
> old_domain, new_domain, sub_category, chunks_moved}`. For bulk
> moves use `pkb_recategorize_bulk`.

### `pkb_rate` (Phase 5)

> Record a sentiment rating on a Claim node. Sentiment ∈ {-1, 0, 1}
> (negative, neutral, positive). Optional `note` captures rationale.
> **Use when** the user reacts to a specific claim (e.g. 'this is
> wrong', 'verified'). **Returns** `{rated, claim_id, sentiment, ts}`.
> Errors -32004 if the claim doesn't exist. Creates `:RATED` edges
> that feed `trust_score.user_agreement` directly — no other plumbing
> required.

## Anti-patterns

| Don't | Do |
|---|---|
| `"Search the KB"` | `"Hybrid search across N domains. **Use when**…"` |
| `"Returns results"` | `"**Returns** `{results: [...], total, confidence}`"` |
| `"Runs maintenance"` | `"MUTATING maintenance routines: …"` (call out side effects) |
| Markdown headings inside description | Plain inline `**Use when**` / `**Returns**` bold spans |
| Verb without subject ("Returns results", "Lists tools") | Subject + action ("List ingested artifacts", "Returns `{...}`") |

## Cost-class hint

`@register_tool(..., cost_class=…)` surfaces a coarse hint to clients:

* `"low"` — pure-local read; p95 < 200 ms (`pkb_health`, `pkb_artifact_get`)
* `"medium"` — single LLM/embed call; p95 < 2 s
  (`pkb_summarize_artifact`, `pkb_extract_entities`)
* `"high"` — chained LLM + retrieval + reranking; p95 up to 8 s
  (`pkb_answer_with_citations`, `pkb_summarize_domain`)

LLM clients can avoid `cost_class="high"` tools when latency-sensitive.
Cost class is a hint, not a contract; the latency-budget tests in
Phase 2.1 monitor reality.

## Deprecation metadata

When a tool is superseded, set:

```python
@register_tool(
    name="pkb_old",
    ...,
    deprecated_since="0.95.0",
    deprecated_replaced_by="pkb_new",
)
```

The schema includes `_deprecated_since` + `_deprecated_replaced_by`
extension fields. Description should start with **"DEPRECATED — prefer
`pkb_new`."** so the LLM routes away from it.

Removal target: ≥ one minor release after deprecation ships, to give
downstream callers time to migrate.
