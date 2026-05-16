# MCP tool test bundle convention

When adding a new tool to cerid-kb, ship the following test files
alongside the handler so it lands with a complete safety net.

## Minimum per-tool test bundle

### 1. Schema fidelity (auto-generated)

`tests/test_mcp_tool_schema_fidelity.py` is parametrised over every
tool returned by `get_all_tools()`. Six invariants run per tool:

* `inputSchema` and `outputSchema` both exist
* Both have `type == "object"` (per MCP 2024-11-05+)
* `inputSchema.required` references only declared properties
* Both are valid JSON Schema (Draft-7 accepted)

Adding a new tool via `@register_tool(...)` enrols it in this gate
automatically — no per-tool test file needed for this invariant.

### 2. Handler unit test

`tests/test_mcp_tools_<category>.py` (e.g.
`test_mcp_tools_fundamentals.py`). One file per `mcp_tools/` module
that exercises:

* **Happy path** with mocked upstream deps
* **Error path** (raises the typed error class — `InvalidParamsError`,
  `ResourceNotFoundError`, etc. — see `app/tool_registry.py`)
* **Edge cases**: empty input, oversized input, defaults

Mock upstream deps at the SOURCE — `app.mcp_tools.<module>.get_neo4j`
not `app.deps.get_neo4j` — so the test doesn't accidentally exercise
the real dependency.

### 3. Description quality (auto-checked)

`scripts/lint-mcp-descriptions.py` validates every tool's description
contains `Use when` and `Returns` anchors per `MCP_TOOL_STYLE.md`.

Warn-only in v0.95.x CI; promoted to blocking in v0.96.

### 4. Integration test (optional)

`tests/integration/test_mcp_<tool>.py` if the tool's value depends on
end-to-end behaviour that mocks can't verify (e.g. real Neo4j
schema matches, real ChromaDB collection state, GDS plugin reachable).
Mark with `@pytest.mark.requires_live` so it runs only when the dev
stack is up.

## Test discovery

Tests live under `src/mcp/tests/`. The full suite runs via:

```bash
PYTHONPATH=src/mcp .venv/bin/pytest src/mcp/tests/ -q
```

CI runs the same path under `lint / mcp-tool-schema-fidelity`. The
gate is wired into the `docker` job's `needs[]` so the full pipeline
blocks on schema regressions.

## Latency budget tests (v0.96+ planned)

`@register_tool(..., cost_class=...)` declares a budget that a future
contract test will enforce:

```python
# tests/test_mcp_tool_budgets.py — planned v0.96
def test_pkb_artifact_get_p95_under_budget():
    samples = [time_call('pkb_artifact_get', ...) for _ in range(100)]
    assert percentile(samples, 95) < 200  # 'low' cost class
```

Until that lands, the `/health.invariants.mcp` aggregate is the
visibility surface — operators eye-ball p95 against the
`cost_class` budgets in `MCP_OBSERVABILITY.md`.
