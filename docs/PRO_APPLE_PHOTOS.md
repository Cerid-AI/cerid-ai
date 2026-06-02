# Apple Photos connector

## What this connector does

The Apple Photos connector reads **metadata** from your local Photos library
through Apple's PhotoKit and makes it queryable from your Cerid knowledge
base, so you can ask things like "when was I in Lisbon?" from chat. It is
**metadata-only** — it never reads pixel data, decodes images, or runs vision
models on your photos. Everything happens on your Mac.

## One-time setup

The connector reads metadata via the bundled `ceridphotos` Swift helper, which
uses PhotoKit and therefore needs **Photos** access for the Cerid desktop app.

1. Open **System Settings → Privacy & Security → Photos**.
2. Set **Cerid** to **Full Access**. (If Cerid isn't listed yet, open
   **Connectors → Apple Photos** and click **Enable** once — macOS will
   prompt for access, after which Cerid appears in this list.)
3. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start, so a running Cerid will keep showing
   "Needs access" until you restart it.

Once relaunched, open **Connectors → Apple Photos** and click **Enable**.

## What gets ingested

Per-asset **metadata only**:

- Capture date and time.
- Location (latitude/longitude) when the photo has it.
- Dimensions and media subtype (e.g. photo, video, Live Photo, screenshot).
- Favorite and hidden flags.

Assets are surfaced with `source: apple_photos`.

## What's NOT ingested

- **Pixel data.** The connector never reads, copies, decodes, or analyzes the
  image or video content itself — only the metadata fields above.
- Faces, recognized people, or any Photos "Memories" / ML-derived groupings.
- Photos in libraries or accounts you have not granted access to.

## Privacy posture

All access is local and read-only through PhotoKit, and limited to metadata.
The connector makes no network calls and never sends image bytes anywhere.
When you later ask the agent a question, only the retrieved metadata snippets
used to answer that specific query are sent to whichever LLM you've configured.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Photos **Full Access** to Cerid, then **quit and relaunch the app**.
Toggling the permission while Cerid is running does not take effect until
restart.

**No assets after enabling.**
Confirm the `ceridphotos` helper shipped with your build (it is bundled in the
signed desktop app) and that the System Settings → Photos toggle is set to
**Full Access** rather than **Limited**. Re-run the scan after granting access.
