# Messages (iMessage / SMS) connector

## What this connector does

The Messages connector reads your local `chat.db` and ingests the
conversations **you explicitly opt into**, one chat at a time. Unlike
Notes and Mail — which scan everything by default — this connector
never reads a conversation you haven't enabled. Useful for surfacing
context from long-running threads (project discussions, family
logistics, support exchanges) without dragging in every message you've
ever sent.

## One-time setup

The connector needs two macOS permissions:

1. **Full Disk Access** — to read `~/Library/Messages/chat.db`.
2. **Contacts** — so phone numbers and Apple IDs can be resolved to
   the names you actually recognize. Without Contacts, conversations
   show raw handles (`+1415…`, `apple-id@…`).

Steps:

1. **System Settings → Privacy & Security → Full Disk Access** —
   add `/Applications/Cerid.app` and turn it on.
2. **System Settings → Privacy & Security → Contacts** —
   add Cerid and turn it on.
3. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start. Without a restart, Cerid will still
   see "Needs access" for both.

Once relaunched, open **Connectors → Messages**. You'll see a list of
your recent conversations (1:1 and group), each with an Enable toggle.
Flip on the ones you want indexed. Nothing is ingested for a
conversation until you enable it.

## What gets ingested

Per-conversation opt-in — only enabled chats are read. For an enabled
chat:

- **Every message** in that chat, including history that predates the
  enable action.
- **Group chats are supported** — every participant's messages are
  ingested when the chat itself is enabled.
- **Message body** is taken from `message.text` when present.
  Starting with macOS Ventura the `text` column is empty for many
  messages; the connector falls back to decoding `attributedBody`
  (Apple's `NSKeyedArchiver` blob) to recover the text. Reactions and
  tapbacks are preserved as text annotations.
- **Sender, timestamp, chat name** — surfaced as artifact metadata so
  citations stay grounded.

Each message becomes part of a per-conversation artifact tagged with
`source: imessage`.

> **Status:** the connector currently surfaces recent messages via the
> `ceridimessage` helper's `scan`. Per-conversation opt-in enable/disable and
> the `attributedBody` body recovery described below land with the helper's full
> `chat.db` reader — no reconfiguration is needed when it does; the same Full
> Disk Access grant and `private_mode` Level 2 floor cover it.

## What's NOT ingested

- **Any conversation you haven't explicitly enabled.** This is the
  whole point of the opt-in model. Disabling a previously enabled
  chat also removes its artifacts from the KB on the next sync.
- **Attachments** — images, voice notes, links to shared files.
  Only message text is read.
- **Read receipts, typing indicators, and other ephemeral signals.**

## Privacy posture

All parsing is local. The connector reads `chat.db` read-only; nothing
is sent over the network.

Messages are subject to a stricter retrieval policy than other
sources: **`private_mode` Level 2 or higher is required at query time**
for chat content to surface in an answer. If you ask a question in
the default privacy level, the agent can tell you *that* a
conversation exists and *who* it's with, but it will not quote message
text or include it in the synthesized answer. Raise privacy level for
the query (or session) to allow chat bodies into the response.

This is intentional: messages are the most sensitive corpus most
people own, and we don't want a casual question to splash a private
conversation into the answer pane.

## Where the data lives on disk

The connector reads from (read-only):

```
~/Library/Messages/chat.db
```

This is Apple's own SQLite store. The connector never writes to it.

## How retrieval surfaces it

When a message is used to answer a question (at sufficient privacy
level), the citation chip shows `source: imessage` along with the
chat name and the sender's contact name. Clicking the chip opens the
artifact and shows the message in context — the surrounding turns
from the same chat, not just the single matched message.

## Troubleshooting

**"Needs access" banner persists.**
Grant **both** Full Disk Access **and** Contacts to Cerid, then
**quit and relaunch the app**. The TCC cache only re-reads
permissions on process start.

**Conversations show raw phone numbers instead of names.**
Contacts permission is missing or was added after Cerid started.
Add it, then relaunch Cerid.

**"0 messages ingested" for a chat that obviously has messages.**
Has Messages.app been opened on this Mac at least once and signed
in? `chat.db` is created the first time Messages runs. Open
Messages.app, let it finish initial sync, then re-enable the chat.

**Messages from recent macOS show empty bodies.**
Ventura and later moved many message bodies out of the `text`
column into `attributedBody`. The connector handles this — if you're
still seeing empty bodies, file a report with the macOS version
shown in **About This Mac**.

**A chat I disabled is still showing up in answers.**
Toggling a chat off marks its artifacts for removal on the next
sync cycle. Run a manual sync from the connector pane to apply the
deletion immediately.
