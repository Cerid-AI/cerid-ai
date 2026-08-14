# Apple Reminders connector

## What this connector does

The Apple Reminders connector reads your local Reminders through Apple's
EventKit and ingests them into your Cerid knowledge base, so you can ask
about your to-dos and reminder lists from chat. Everything happens on your
Mac — reminder data never leaves the machine.

It runs entirely inside the **Cerid desktop app**: the app's main process
invokes the bundled `ceridreminders` Swift helper and posts each reminder to
your local knowledge base. (There is deliberately no server-side surface —
the MCP server runs in a Linux container, which can never execute a macOS
helper.) A browser session shows the source as desktop-only.

## One-time setup

The helper uses EventKit and therefore needs **Reminders** access for the
Cerid desktop app.

1. Open **System Settings → Privacy & Security → Reminders**.
2. Enable the toggle next to **Cerid**. (If Cerid isn't listed yet, open
   **Sources → Connectors → Apple Reminders** and click **Sync to KB** once —
   macOS will prompt for access, after which Cerid appears in this list.)
3. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start, so a running Cerid will keep showing
   "Needs access" until you restart it.

Once relaunched, open **Sources → Connectors → Apple Reminders**, review the
scan summary, and click **Sync to KB**.

## What gets ingested

For each reminder the helper can read:

- Title and notes (the reminder body).
- Due date, completion state, priority, and the list it belongs to.

Reminders are surfaced with `source: apple_reminders`.

## What's NOT ingested

- Reminder lists you have not granted access to.
- Attachments on reminders.
- Shared lists owned by other iCloud accounts you have not added on this Mac.

## Privacy posture

All access is local and read-only through EventKit. The connector makes no
network calls beyond posting to your own local (or LAN) Cerid server. When
you later ask the agent a question, only the retrieved snippets used to
answer that specific query are sent to whichever LLM you've configured.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Reminders access to Cerid, then **quit and relaunch the app**. Toggling
the permission while Cerid is running does not take effect until restart.

**No reminders after syncing.**
Confirm the `ceridreminders` helper shipped with your build (it is bundled in
the signed desktop app) and that at least one list exists in Reminders.app.
Re-run the scan after granting access.
