# Apple Mail connector

## What this connector does

The Apple Mail connector reads your local Mail.app message store and
ingests email bodies into your Cerid knowledge base so you can search and
ask questions across your mail without going through a cloud API. The
connector talks to the on-disk store Mail.app already maintains — no IMAP
credentials, no OAuth flow, no provider-specific setup.

## One-time setup

Reading the Mail envelope index and `.emlx` files requires **Full Disk
Access** for the Cerid desktop app.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+**, navigate to `/Applications/Cerid.app`, and add it.
3. Make sure the toggle next to Cerid is **on**.
4. **Quit and relaunch Cerid.** The kernel's TCC cache only refreshes on
   process start; without a restart Cerid will still see "Needs access".

Once relaunched, open **Connectors → Apple Mail** and click **Enable**.
The first scan walks every account and mailbox you have configured in
Mail.app.

## What gets ingested

For each message:

- **From** — sender display name and address.
- **Subject** — the message subject line.
- **Body** — the plain-text part of the message. If the message is
  HTML-only, the HTML is stripped down to readable text before
  ingestion.
- **Date** — the message timestamp from the envelope index.
- **Mailbox path** — e.g. `iCloud / Inbox`, `Work / Sent`,
  `Local / Archive 2024`.

Each message becomes a single artifact tagged with `source: apple_mail`.

Recipient extraction (`To`, `Cc`, `Bcc`) is deferred to v1.1 — current
ingestion focuses on the fields that drive retrieval quality (subject +
body + sender).

## What's NOT ingested

- **Attachments.** PDFs, images, and other attached files are not
  parsed. Only the message body itself is read. (Attachment ingestion
  may land in a later release behind a separate opt-in.)
- **Messages that haven't been downloaded yet.** If Mail.app is set to
  "download headers only" for an account, only the headers exist
  locally — the body is fetched on-demand from the server and is not
  visible to the connector.
- **Junk and Trash folders.** These are skipped by default.

## Privacy posture

All parsing is local. The connector reads the envelope SQLite index and
the `.emlx` files Mail.app already wrote to disk; nothing is sent over
the network. The artifacts produced live in your local knowledge base.
When you later query the agent, only the snippets retrieved for that
specific question are sent to the LLM you've configured.

## Where the data lives on disk

The connector reads from (read-only):

```
~/Library/Mail/V10/MailData/Envelope Index
~/Library/Mail/V10/<account-uuid>/<mailbox>.mbox/.../Messages/*.emlx
```

`V10` is the current Mail.app store version on supported macOS releases;
the connector falls back to older `Vn` directories if `V10` is absent.
The connector never writes to these paths.

## How retrieval surfaces it

When an email is used to answer a question, the citation chip shows
`source: apple_mail` along with the sender and subject. Clicking the
chip opens the artifact so you can see the exact body text that was
retrieved, plus the mailbox path it came from.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Full Disk Access to Cerid, then **quit and relaunch the app**.
Toggling the permission while Cerid is running does not take effect
until restart.

**"0 messages ingested" after a successful scan.**
Has Mail.app been opened on this Mac at least once and finished an
initial sync? The envelope index and `.emlx` tree are only created
once Mail.app has actually downloaded messages. Open Mail, wait for it
to populate, then re-run the scan.

**A specific account shows zero messages but others work.**
That account is probably set to "download headers only" or is
server-side-only (some Exchange / corporate setups). The connector can
only see messages whose bodies are on disk; ask Mail to download full
messages for that account, then re-scan.

**HTML messages look messy in the artifact view.**
The stripper preserves text and structure but drops tracking pixels,
inline styles, and most formatting markup. The retrieval engine works
on the cleaned text, not on the original HTML.
