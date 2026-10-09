# Private Mode

Private Mode is one global level, 0 to 4, stored in Redis
(`cerid:private_mode:global`) and set with `POST /settings/private-mode`
(`PrivateModeRequest.level`, `app/routers/settings.py`). Every server gate reads
the level from Redis, never from the request, so a direct API, SDK or MCP caller
gets the same guarantee the web client applies locally. Each level keeps what
the level below withholds.

Available on every tier; every level.

**No level blocks requests to the model provider: what you type still goes to
the configured model. Use a local provider for that.**

The table below is the contract. Its source is
`src/web/src/lib/private-mode-levels.json`, which the settings page, the
settings registry, the chat toolbar and the capability text render from;
`src/mcp/tests/test_private_mode_contract.py` fails when this table, that file
or the server's level ladder disagree.

## Levels

| Level | Name | Retrieval | Memory | Server | Browser | Audit | Egress |
|---|---|---|---|---|---|---|---|
| 0 | Off | Knowledge-base and memory context are injected as usual. | Memories are extracted after qualifying turns and verified facts are promoted. | Conversations are saved and synced; verification reports, verified facts and claim feedback are stored. | Conversations are cached in this browser's local storage. | The MCP tool-call audit line is written. | The request goes to the configured model provider. |
| 1 | Skip saves & sync | Unchanged: knowledge-base and memory context are still injected. | Withheld: memory extraction and verified-fact promotion return without storing anything. | Withheld: conversation saves, sync and deletes are refused; verification reports are not written to Redis or Neo4j; SDK claim feedback is dropped. | Withheld: the conversation is held in memory only, never written to local storage, and is gone on reload. | Unchanged: the MCP tool-call audit line is written. | Not blocked: the chat still goes to the configured provider. Under the hybrid or cloud-first profile, internal pipeline stages pinned to the cloud run on the local provider instead. |
| 2 | Also skip the knowledge base | Withheld: no knowledge-base or memory context reaches the model. The server strips injected context from chat and SDK completions; KB search, memory recall and wiki enrichment return empty. | Withheld: memory extraction and verified-fact promotion return without storing anything. | Withheld: conversation saves, sync and deletes are refused; verification reports are not written to Redis or Neo4j; SDK claim feedback is dropped. | Withheld: the conversation is held in memory only, never written to local storage, and is gone on reload. | Unchanged: the MCP tool-call audit line is written. | Not blocked: what you type still goes to the configured provider. |
| 3 | Also skip the audit line | Withheld: no knowledge-base or memory context reaches the model. The server strips injected context from chat and SDK completions; KB search, memory recall and wiki enrichment return empty. | Withheld: memory extraction and verified-fact promotion return without storing anything. | Withheld: conversation saves, sync and deletes are refused; verification reports are not written to Redis or Neo4j; SDK claim feedback is dropped. | Withheld: the conversation is held in memory only, never written to local storage, and is gone on reload. | Withheld: the MCP tool-call audit line (mcp.tool_call) is not written. Still recorded: metrics counters in Redis, the enterprise audit log and ordinary server logs. | Not blocked: what you type still goes to the configured provider. |
| 4 | Full ephemeral | Withheld: no knowledge-base or memory context reaches the model. The server strips injected context from chat and SDK completions; KB search, memory recall and wiki enrichment return empty. | Withheld: memory extraction and verified-fact promotion return without storing anything. | Withheld as at L1. When an L4 tab closes, the browser asks the server to forget that tab's private conversations permanently, with whatever memories and verified facts they produced before the tab reached L4, and keeps a receipt. When the last L4 tab closes the level resets to 0. Cached query results are not wiped; they expire on their own. | Withheld: the conversation is held in memory only, never written to local storage, and is gone on reload. | Withheld: the MCP tool-call audit line (mcp.tool_call) is not written. Still recorded: metrics counters in Redis, the enterprise audit log and ordinary server logs. | Not blocked: what you type still goes to the configured provider. |

## Where each level is enforced

- **L1, saves.** `app/services/private_mode.py` (`SKIP_SAVES_LEVEL`,
  `saves_blocked`). Conversation save, bulk save and delete:
  `app/routers/user_state.py`. Memory extraction: `app/routers/agents.py`
  (`/agent/memory/extract`, `/agent/memory/extract-recent`) and
  `app/tools.py` (`pkb_memory_extract`); verified-fact promotion:
  `agents.py::_verified_memory_fn`. Verification reports:
  `persist_report=not saves_blocked()` in `agents.py`, `app/tools.py` and
  `app/routers/a2a.py`. SDK claim feedback: `app/routers/feedback.py`.
  Browser: `hooks/use-conversations.ts` (no server sync, private
  conversations never written to local storage) and `hooks/use-chat.ts` (no
  feedback-loop ingest, no post-turn memory extraction). Internal LLM stage
  rerouting: `core/utils/internal_llm.py` (`_PRIVATE_MODE_CLOUD_CUTOFF`).
- **L2, knowledge base.** `core/agents/request_context.py`
  (`PRIVATE_MODE_SKIP_KB_LEVEL`, `RequestContext.blocks_kb`), built in
  `app/services/request_policy.py`. Generation boundary:
  `strip_injected_context` in `app/routers/chat.py` and `app/routers/sdk.py`.
  Empty results: `app/routers/query.py`, `app/routers/agents.py`
  (`/agent/query`), `app/routers/sdk.py` (`/sdk/v1/search`, memory recall),
  `core/agents/guarded_retrieval.py`. Third-party lookups:
  `app/services/external_apis/wiki_enrichment.py`. Browser:
  `hooks/use-chat-send.ts` (`bypassKB`).
- **L3, audit.** `app/tools.py`: `if not private_blocks(3)` around the
  `mcp.tool_call` audit line. Metrics (`utils/metrics.py`) and the enterprise
  audit log (`docs/ENTERPRISE_AUDIT_LOG.md`) are not gated.
- **L4, ephemeral.** `POST /settings/private-mode/session-wipe`
  (`app/routers/settings.py`) forgets the tab's private conversations through
  the forget engine (`app/services/forget/`) with a receipt; the browser sends
  it from `hooks/use-private-session-wipe.ts` on `beforeunload`, with the tab's
  session id (registered by `POST /settings/private-mode`) and the API key. In
  multi-user mode only an admin's wipe forgets; a member's releases the tab.

## Known gaps

These are the current behaviour, stated so the contract above is true. They
are tracked for a ruling, not fixed by the contract.

- Thumbs-rating feedback (`POST /ingest/feedback` from the message bubble),
  `POST /agent/hallucination/feedback` and `/agent/memory/archive` are not
  gated at any level.
- iMessage visibility does not depend on the level; it is the separate
  `SENSITIVE_DOMAIN_RETRIEVAL_ENABLED` toggle (`docs/PRO_MESSAGES.md`).
