# Phase G — Deferred Native Extensions

Phase G ships three SPM Swift CLI helpers (`ceridek`, `ceridphotos`,
`ceridspotlight`) that cover EventKit, PhotoKit, and CoreSpotlight
respectively. Three other native targets from the original Phase G plan
are explicitly deferred to a follow-up sprint because they require
Xcode infrastructure that SPM cannot produce.

## What's deferred

| Target | Apple requirement | Why SPM-only fails |
|---|---|---|
| **App Intents** (Shortcuts.app discovery) | `Metadata.appintents` artifact emitted by the Swift compiler under Xcode | SPM's `executableTarget` doesn't emit the metadata; the system Shortcuts scanner reads it from the parent `.app` bundle. |
| **Share Extension** (`.appex`) | Bundle type = `appex`, embedded in `Contents/PlugIns/` | SPM has no `appex` product type. Manual plist + codesign sequencing works (community recipe) but is fragile across Xcode releases. |
| **Quick Look Generator Extension** (`.appex`) | Same | Same |

## Path forward

When the follow-up sprint lands, these three live in
`packages/desktop/macos/` as `.xcodeproj` projects driven by `xcodebuild`
from CI. The `electron-builder` `afterPack` hook embeds the resulting
`.appex` bundles under `Cerid.app/Contents/PlugIns/` *before*
notarization (extensions must be sealed under the parent app's
signature).

### Order of operations the follow-up will follow

1. Create `packages/desktop/macos/CeridShareExt.xcodeproj` with one
   target producing `CeridShareExt.appex`. Bundle ID
   `ai.cerid.desktop.ShareExtension`. App Group entitlement
   `group.ai.cerid.desktop` (matches main app, already in
   `entitlements.mac.plist`).
2. Same for `CeridQuickLook.xcodeproj` (`com.apple.quicklook.preview`
   extension point, generates `.cerid-artifact` / `.cerid-meeting`
   previews).
3. Same for `CeridAppIntents.xcodeproj` — produces a framework, not an
   extension. Embedded under `Contents/Frameworks/` of the Electron
   main bundle. App Intents discovered automatically by Shortcuts.app
   when the framework's `Metadata.appintents` is present.
4. Add a `make build-extensions` step that invokes `xcodebuild` for
   each project. CI runs it on macOS-latest runners with the
   Developer ID Application cert imported.
5. `afterPack` hook copies the built `.appex` files into
   `Contents/PlugIns/` and the framework into `Contents/Frameworks/`,
   then re-signs the parent `.app` with `--deep` so the extension
   bundles are sealed inside.
6. Notarization picks up the embedded extensions automatically (one
   submission covers everything).

### Signing order (load-bearing)

```
1. All embedded dylibs in each .appex
2. The .appex binary
3. The .appex bundle
4. (Electron-builder's normal step) All embedded dylibs in the .app
5. The Electron Helper binaries
6. Cerid.app itself with --deep
```

Out-of-order signing causes Gatekeeper rejection at first launch on a
fresh Mac. The Phase G follow-up must add `codesign --verify --deep
--strict` as a CI gate to catch this regressing.

## Why we drew the line here

The SPM-built CLI helpers handle 80% of the user-visible Apple
ecosystem value (Calendar, Reminders, Photos metadata, Spotlight
results) with infrastructure simple enough to maintain in one
afternoon. The remaining 20% (Share Sheet + Quick Look + Shortcuts
voice integration) requires a parallel Xcode build pipeline that costs
roughly the same effort to set up as everything in Phase G combined.
That trade is worth making once the SPM trio proves the user-experience
hypothesis — if Calendar/Reminders/Spotlight donation get used, the
Xcode investment is justified. If not, we save the build complexity.
