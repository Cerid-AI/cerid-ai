# Pro — AI Inbox Triage

Categorize Gmail, Outlook, and Apple Mail. Classification stops at the
first confident rung: a sticking rule, then the small local model, then
the heavy local model, then the frontier model. The frontier model does
not run under the local-only profile. A draft uses the frontier draft
stage only when the local draft checklist fails.

Cerid runs every 15 minutes when both gates below are open. Each thread
becomes a ledger proposal. A knowledge-base card is written only when the
utility says the thread is worth keeping.

## What this does

| Field | Meaning |
|---|---|
| `category` | `urgent`, `actionable`, `personal`, `newsletter`, `promo`, or `spam` |
| `action` | `keep`, `archive`, `mark_read`, or `draft` |
| `utility` | `none`, `correspondence`, or `financial` |
| `summary` | One-sentence paraphrase |
| `suggested_action` | Short phrase for the review queue |

Closed actions are `keep`, `archive`, `mark_read`, `draft`, and `undo`.
Transmit, forward, discard, and spam are not actions.

The categorization rules stay conservative: only a clear same-day deadline
is `urgent`. The fallback category is `actionable`.

Utility decides the knowledge base, separate from the category:

| Utility | What is stored |
|---|---|
| `none` | The ledger row only. No body. |
| `correspondence` | An excerpt in domain `inbox`. `record_type` is `mail_thread`. |
| `financial` | A short card in domain `finance` (`record_type` `mail_financial_card`) and a one-line pointer in domain `inbox` (`record_type` `mail_finance_pointer`). |

The card names payee, amount, currency, date, subject, and the provider
thread, and only the fields the message states. The raw body never enters
`finance`. cerid-finance is not granted domain `inbox`. Mail does not
create finance transactions, balances, or recurring rules.

Apply does not ingest. The route hook only accepts or rejects a post the
triage pass already built. Payload kind before that hook: `none` → none,
`correspondence` → excerpt, `financial` → card.

## How classification runs

| Stage | When | Where |
|---|---|---|
| deterministic | A pin, a rule, or a sticking verdict at or above 0.8 | No model |
| `inbox_triage` | The first model rung. A failed call is retried once on this same stage. A second reply that names no category is repaired once here, asking for the category only | Small local model, unless `PROVIDER_STAGE_INBOX_TRIAGE` pins a provider |
| `inbox_triage_review` | A parsed category is below 0.8. Also the draft text after classification | Heavy local chat slot |
| `inbox_triage_escalate` | The heavy local model is below 0.8 or failed, or the category repair failed or is still below 0.8 | Frontier model. Skipped under local-only; the thread stays `needs_review` |
| `inbox_triage_draft` | The local draft failed its checklist | Cloud. Skipped under local-only; no draft body |

A rung at 0.8 stops. A frontier answer that is still below 0.8 stays
`needs_review` with that answer attached. A miss that names no category
counts as a failed rung. The small stage retries that miss once. A second
miss gets one short repair on that same stage, asking for the category
enum only. A repair at 0.8 stops. A failed repair, or a category below
0.8, climbs to the frontier and does not call the heavy rung. A parsed
category below 0.8 still uses the heavy rung.

A draft is a second model call. It is not auto-applied. Under local-only
a failed draft is `needs_review` with no draft body. A `needs_review` draft
that does have a body stays in the review queue as `proposed`.

Sender memory runs before any model. Three identical corrections of the
same action and category pin that sender. A different outcome resets the
count to 1 and clears the pin. A manual pin (`pkb_inbox_sender_pin`) is
immediate and is not sticky across a changed outcome. One or two unpinned
hits do not skip the model. `pkb_inbox_rule_upsert` stores a rule on
`from`, `domain`, `subject_prefix`, or `list_id`. A pin or a matching rule
skips the model, including when the action is `draft`. A subject prefix
matches the title as stored; an Apple title that starts with `Mail: ` does
not match a prefix of the words after that.

When the operator clears the Cerid label, category, or flag and the
message is back in the inbox, the learned outcome is `keep` /
`actionable`. Several Cerid labels on one message are not learned. The
same visible outcome as the applied decision is not a new correction.

## Filing

`CERID_INBOX_ACTIONS_ENABLED` defaults off. With it off, apply reports
`disabled` and writes nothing to the mailbox. `pkb_inbox_apply` defaults
to dry-run.

Per-account `auto_apply` defaults to no categories. Auto-apply files only
`keep`, `archive`, and `mark_read`, oldest first, at most 50 decisions a
pass. Drafts and `needs_review` rows are not auto-applied.

Folder sorting is off by default, per account. Off: `keep` labels or flags
and does not move. On: `keep` files into `Cerid/<Category>`, including
`Cerid/Spam`. `archive` is the provider Archive, including for spam.
The provider Junk and Trash mailboxes are never destinations. Undo records
the previous location.

Spam is unsolicited or deceptive mail. A sale with an unsubscribe link
stays `promo`. Mail the provider already filed into Junk is left there
and is not read back.

Classification also reads headers the fetch already returned. `List-Id`
or `List-Unsubscribe` files the thread as `newsletter`, unless the
subject is urgent or the text is a sale. Gmail labels
`CATEGORY_PROMOTIONS`, `CATEGORY_UPDATES`, and `CATEGORY_FORUMS` do the
same when the content reply includes them. `CATEGORY_SOCIAL` and
`CATEGORY_PERSONAL` are a hint, not a filing. `X-Spam-Flag: Yes` is
spam. A DMARC fail does not archive the message. It sends an otherwise
urgent thread through the review model. A sender pin still wins, and a
bill is not filed as spam on a score alone.

Rspamd is off until `CERID_RSPAMD_URL` is `http://127.0.0.1:11333`.
Homebrew has no rspamd formula. From the repo root, start the scanner
with `docker compose -f stacks/rspamd/docker-compose.yml up -d`.
Cerid posts a reconstructed message to `/checkv2` on loopback only.
The request disables RBL, SURBL, fuzzy, and authentication groups, so
the scan does not leave the machine. Any other host is ignored. A
`reject` action, or a score at the daemon's required threshold, files
`spam`. `add header` does not archive by itself. If rspamd is down,
triage continues on the phrase list and the headers.

`scripts/inbox_review.py` is the read-only IMAP check. It does not write
the ledger, and production triage does not read Junk. Set
`CERID_INBOX_REVIEW_REDACT` to a comma-separated, case-insensitive list
of substrings and the script leaves out any message whose From, To, or
Subject contains one; it is empty by default. Rspamd stays a
local content score. A phrase promo or newsletter sticks. Urgent markers
are read from the subject.

Labels: `Cerid/Urgent`, `Cerid/Action`, `Cerid/Personal`,
`Cerid/Newsletter`, `Cerid/Promo`, `Cerid/Spam`. The actionable folder is
`Cerid/Action`. Apple flags: urgent red, actionable orange, personal blue,
newsletter green, promo purple, spam gray.

Gmail and Outlook learn a correction only when the message metadata
carries labels, categories, or a well-known folder. Gmail's content fetch
does not return labels, and the unread triage query often will not see a
message you already archived or marked read. Outlook copies categories and
a well-known folder (`inbox`, `archive`, `drafts`, `sentitems`) when the
list payload includes them. A GUID folder id is not copied. Apple
mailbox-moved-back is learned from the mailbox on the message compared
with the recorded move.

## Addresses

Sources → Connectors holds more than one address per provider. Gmail keeps
`--single-user`. An extra Gmail address stays pending: an apply whose
account differs from `USER_GOOGLE_EMAIL` is skipped. Outlook and Apple
fetches often carry no account. If that provider has exactly one included
address, the proposal uses it. Two or more and no account on the message
means no proposal.

Removing an address stops later reads and applies. It does not delete
knowledge-base cards already written. Undo of a decision for that address
still runs. The account row stays, marked removed.

## How to enable

Two gates — both must be open:

1. **Pro feature flag**: `inbox_triage` is on by default for Pro
   accounts. For self-hosted: set `CERID_TIER=pro` in `.env`.
2. **Operator opt-in**: set `CERID_INBOX_TRIAGE_ENABLED=true` in
   `.env` then restart the MCP container. (This double-gate prevents
   inadvertent LLM cost on every Pro install.)

A toggle set in Settings → Automations (`PUT
/settings/pro-automations/inbox_triage`) is stored in Redis at
`cerid:automations:inbox_triage:{enabled,schedule}` and wins over the env
value until `DELETE /settings/pro-automations/inbox_triage` clears it. The
table of both automations' switches is in
`docs/PRO_DAILY_DIGEST.md` § How to enable.

Mailbox writes are a third switch, `CERID_INBOX_ACTIONS_ENABLED`, default
off. See `docs/PRO_GMAIL.md`, `docs/PRO_OUTLOOK.md`, and
`docs/PRO_APPLE_MAIL.md`. Turning it on requires re-consent. The router
does not start that login.

Prerequisites: at least one of Gmail, Outlook, or Apple Mail configured.

## Cadence

Default: **every 15 minutes** while the toggle is on.

Override via the cron expression env var:

```bash
SCHEDULE_INBOX_TRIAGE="0 */2 * * *"   # every 2 hours
SCHEDULE_INBOX_TRIAGE=""              # disable the cron entirely
```

Each run has these cost guards:

- `INBOX_TRIAGE_MAX_PER_SOURCE=30` — cap on messages fetched per source per run
- `max_instances=1` on the scheduler job so overlapping runs cannot pile up
- Apply files at most 50 decisions a pass
- The read is the connector's existing message fetch: metadata and a short
  excerpt. It does not request a full-format body. Gmail label edits are
  batched

## Querying from chat

**`pkb_inbox_triage`** — runs a fresh triage pass (cost_class=high).

**`pkb_inbox_filter`** — read-only query against already-triaged threads
(no LLM, cost_class=low):

- "what's urgent today?" → `pkb_inbox_filter(category="urgent")`
- "newsletters from this week" → `pkb_inbox_filter(category="newsletter", since_days=7)`
- "actionable Outlook threads" → `pkb_inbox_filter(category="actionable", source="outlook")`

**`pkb_inbox_apply`** — file proposed decisions. Dry-run unless you pass
otherwise. Does nothing while the actions flag is off.

**`pkb_inbox_undo`** — reverse one applied decision.

**`pkb_inbox_sender_pin`** — pin a sender's action and category now.

**`pkb_inbox_rule_upsert`** — store or replace a filing rule.

Citations point at the knowledge-base artifact when one was written
(`artifact_id`). The review queue links a finance card and a
correspondence excerpt when those exist.

## Privacy posture

- The small and heavy classification stages stay local unless a
  `PROVIDER_STAGE_<NAME>` override pins another provider. The frontier
  classification stage and the draft stage are the cloud calls, and
  neither runs under the local-only profile.
- Each model call sees one thread excerpt. No bulk mailbox is sent in a
  single call.
- Privacy filters still apply. A privacy-gated domain cannot surface in
  retrieval at a lower private_mode.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Scheduler logs "0 threads" repeatedly | No unread mail in the last day on a configured source | Expected. A manual `pkb_inbox_triage` can pass a wider `query` |
| `feature_gated` in the skipped list | Pro flag off | Set `CERID_TIER=pro` and restart |
| `not_configured` in the skipped list | That source has no completed handshake | See the connector doc for that source |
| Apply returns `disabled` | `CERID_INBOX_ACTIONS_ENABLED` is off | Set it, recreate the sibling container, and re-consent. A restart is not enough |
| Apply returns `pending_account` | Gmail address is not `USER_GOOGLE_EMAIL` | Expected for an extra Gmail address. It is not filed |
| Draft sits in the queue with no body | Local-only profile, or the cloud draft failed the checklist | Review it by hand. Auto-apply will not file a draft |
| A correction never pins | The pass did not see labels, categories, or the mailbox | Gmail's content tool does not return labels, and an archived message drops out of the unread fetch. See Filing above |
| Cron not firing | `CERID_INBOX_TRIAGE_ENABLED` unset, or empty `SCHEDULE_INBOX_TRIAGE` | Verify both, restart the MCP container |
