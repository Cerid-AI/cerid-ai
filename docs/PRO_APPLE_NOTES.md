# Apple Notes connector

## What this connector does

The Apple Notes connector reads your local Notes database and ingests every
non-encrypted note into your Cerid knowledge base so you can ask questions
across years of notes from chat. Everything happens on your Mac — note text
never leaves the machine.

## One-time setup

The connector reads `NoteStore.sqlite` directly, which requires **Full Disk
Access** for the Cerid desktop app.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+**, navigate to `/Applications/Cerid.app`, and add it.
3. Make sure the toggle next to Cerid is **on**.
4. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start, so a running Cerid will still see
   "Needs access" until you restart it.

Once relaunched, open **Connectors → Apple Notes** and click **Enable**.
The first scan can take a few minutes for large note stores; subsequent
scans are incremental.

## What gets ingested

For every non-encrypted note:

- The plain-text body of the note (rich formatting flattened).
- The folder path the note lives in (e.g. `Notes / Work / Research`).
- The account the note belongs to (`iCloud`, `On My Mac`, an Exchange
  account, etc.).
- The note title and the last-modified timestamp.

Each note becomes a single artifact in the knowledge base, tagged with
`source: apple_notes`.

## What's NOT ingested

- **Encrypted note bodies.** If you have password-protected notes, the
  connector counts them and skips them. Their titles are not surfaced
  either — the database row exists, but the content stays encrypted at
  rest and nothing about it enters the KB.
- **Attachments inside notes** (images, scanned PDFs, drawings). Only the
  text content is read.
- **Deleted notes**, including notes still in the Recently Deleted folder.

## Privacy posture

All parsing is local. The connector opens the SQLite database read-only,
extracts text, and writes artifacts into your local knowledge base. No
network calls are made by the connector itself. When you later ask the
agent a question, only the retrieved snippets used to answer that
specific query are sent to whichever LLM you've configured.

## Where the data lives on disk

The connector reads from (read-only):

```
~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite
```

This is Apple's own store. The connector never writes to it.

## How retrieval surfaces it

When a note is used to answer a question, the citation chip under the
answer shows `source: apple_notes` along with the note title and folder
path. Clicking the chip opens the artifact view so you can see exactly
what text was retrieved.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Full Disk Access to Cerid, then **quit and relaunch the app**.
Toggling the permission while Cerid is running does not take effect
until restart.

**"0 notes ingested" after a successful scan.**
Has the Notes app been opened on this Mac at least once since you signed
into iCloud? `NoteStore.sqlite` is created the first time Notes runs;
on a brand-new account it may not exist yet. Open Notes.app, wait for
iCloud to sync, then re-run the scan.

**"N encrypted (skipped)" in the scan summary.**
Expected. Those are your password-protected notes. The connector
deliberately leaves them encrypted and does not surface their titles.
Unlock them inside Notes.app if you want them indexed (you'll need to
re-run the scan afterward).

**Scan is very slow on first run.**
Large note stores (10k+ notes, lots of long-form content) can take
several minutes. The progress bar updates per batch; let it finish
once and incremental scans will be fast.
