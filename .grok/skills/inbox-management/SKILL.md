---
name: inbox-management
description: Use when classifying, filing, or ingesting mail in Cerid AI — Gmail, Outlook, or Apple Mail actions, the inbox ledger, or a bill card for cerid-finance.
---

# Inbox management

Cerid files mail through a closed action list. The local model returns JSON. It does not receive a mail tool.

Actions: `keep`, `archive`, `mark_read`, `draft`, `undo`. Transmit, forward, and discard are not actions. `spam` is a category, not an action: it archives and never uses the provider Junk mailbox. `List-Id` and `List-Unsubscribe` file as `newsletter` unless the subject is urgent or the text is a sale. Gmail's Promotions label files as `promo`. A local rspamd reject files as `spam` and does not outrank a sender pin or a bill. A sale or a digest sticks. A reply is only a personal hint.

Utilities:

- `none` — decision row only. No body in the knowledge base.
- `correspondence` — excerpt in domain `inbox`.
- `financial` — a short card in domain `finance`, plus a one-line pointer in `inbox`. The raw body never enters `finance`.

`cerid-finance` reads domain `finance` only. A card is how it sees a bill. Do not grant it `inbox`, and do not create transactions or balances from mail.

Folder sorting is off unless the account says otherwise. With it off, `keep` labels or flags and does not move. With it on, `keep` files into `Cerid/<Category>`, including `Cerid/Spam`. `archive` still means the provider Archive. `archive` and `undo` may move.

`pkb_inbox_apply` defaults to dry-run. Sibling tool names `send_gmail_message`, `send-mail`, and `delete-mail-message` are not on the allowlist.

Three identical corrections of one action and category pin that sender. A different outcome resets the count and clears the pin. `pkb_inbox_sender_pin` pins immediately. `pkb_inbox_rule_upsert` stores a from, domain, subject-prefix, or list-id rule. A pin or a matching rule skips the model, including when the pinned action is `draft`.

Classification stops at the first confident rung. A sticking verdict at or above 0.8 skips the model, as does a pin or a rule. Otherwise `inbox_triage` (small local) runs. A failed small call, including a reply that names no category, is tried once more on that same stage. A second miss gets one short local repair that asks for the category only, and does not call the heavy rung. `inbox_triage_review` (heavy local) runs when a parsed category is still below 0.8. Frontier `inbox_triage_escalate` runs when that heavy result is still short, or when the category repair fails or is still below 0.8. A frontier answer below 0.8 stays `needs_review`. The frontier stage does not run under local-only.

A `draft` is a second local call on `inbox_triage_review` after classification. Cloud `inbox_triage_draft` runs only when that checklist fails and the profile is not local-only. Drafts are proposed for review; they are not auto-applied. The two local stages stay local unless `PROVIDER_STAGE_<NAME>` pins a provider.
