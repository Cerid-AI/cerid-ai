# Memory

Material accumulates, stays checked, and can be forgotten.

Cerid AI internal is that memory. Estate direction: `~/dotfiles/docs/estate-direction.md`.

## Owns

- The corpus, citations, contradictions, briefs, lessons, and the inbox ledger.
- Domain, sub-category, and tags. Those fields are the ontology. They live in `src/mcp/config/taxonomy.py` (`TAXONOMY`, `TAG_VOCABULARY`) and, for suite corpora that must stay out of personal retrieval, `OPERATIONAL_ONTOLOGY`. Extend those tables when a stamp is missing. Do not invent a second vocabulary.
- Consumer allow-lists and `record_types`. Finance reads `inbox` only as `mail_financial_card`.
- The operator record (`~/dotfiles/docs/operator-record-and-continuity.md`): pinned facts and history, preferences, rulings, projects, people. A versioned SQLite ledger beside the inbox ledger, not corpus. Pinned entries change only by an operator correction, inferred entries are candidates, conflicts surface as contradiction records, and forgetting goes through the forget engine with a receipt.

## Refuses

- File bytes, device writes, ledger totals, a second model router, and a desktop release train.
- A restricted read that answers with an empty 200. A domain wall is HTTP 403, `retrieval_reason: consumer_domain_restricted`. Private mode on `/sdk/v1` is HTTP 403, `retrieval_reason: private_mode`. A genuine miss is an empty result labeled `retrieval_reason: miss`.

## Wire

Callers use `/sdk/v1` with the product environment and `X-Client-ID`. Anneal writes `POST /sdk/v1/ingest` and reads `POST /sdk/v1/query`. One domain per corpus, because a metadata filter honors one key. Anneal's `metadata.kind` is the sub-category when that kind is in the domain's list.

The operator record is `POST /sdk/v1/operator/brief`, `GET /sdk/v1/operator/entries` and `POST /sdk/v1/operator/entries` (one `op` per call). Grants are each consumer's `operator_record` entry. `X-Client-ID` is required there, and a grant to the personal lane or to any op beyond `propose` and token-proved `confirm` is bound to a credential the caller sends as `X-Operator-Key`. Anneal holds every op and sends its credential, the GUI proposes and confirms with a Deck token in the general lane, the household helper proposes and reads the general lane. An `update_project` that sets `next_action` needs the operator's quote or a Deck token; a state-only change needs neither. A lane other than `personal` never receives a personal entry. A refused write is HTTP 403 with a `reason`.

A suite consumer whose registry entry sets `allow_external` false does not fall through to the web. The preservation harness and the GUI leave the web on.

Model calls walk the ladder that already exists: a script, then the local server on `:11434`, then the stage profile's cloud provider. `MODEL_AUTO_UPDATE_ENABLED` defaults off. A weight shipping is not a reason to move the corpus.

## Ontology

Spec: `docs/superpowers/specs/2026-09-17-suite-contracts-design.md`.

Operational domains stay out of `TAXONOMY`: `trading`, `anneal_lessons`, `anneal_decisions`, `anneal_runs`, `anneal_conversations`, `boardroom_foundation`, `boardroom_routing_examples`, `boardroom_campaigns`, `cerid_ai_business`, and `operator`. `operator` is not corpus (`corpus: False`): ingest refuses it, and no consumer lists it in `allowed_domains`.
