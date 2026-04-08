#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI - macOS Quick Actions & Services Installer
#
# Installs Finder Quick Actions and Services menu integrations that send
# files and selected text to the Cerid KB via the webhook endpoint.
#
# Usage:
#   ./scripts/install-macos-integration.sh               # install all integrations
#   ./scripts/install-macos-integration.sh --quick-action # Finder right-click only
#   ./scripts/install-macos-integration.sh --services     # Services menu only
#   ./scripts/install-macos-integration.sh --uninstall    # remove all integrations

set -uo pipefail

CERID_PORT="${CERID_PORT_MCP:-8888}"
CERID_API="http://localhost:${CERID_PORT}"
CERID_API_KEY_VAL="${CERID_API_KEY:-}"
CERID_SECRET_VAL="${CERID_WEBHOOK_SECRET:-}"
SERVICES_DIR="$HOME/Library/Services"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
RESET="\033[0m"

ok()   { printf "${GREEN}  [ok]${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}  [warn]${RESET} %s\n" "$1"; }
err()  { printf "${RED}  [error]${RESET} %s\n" "$1" >&2; }

check_cerid_running() {
    local health_url="${CERID_API}/health"
    if ! curl -sf -o /dev/null --max-time 5 "$health_url" 2>/dev/null; then
        warn "Cerid API is not reachable at $health_url"
        echo "  Start the stack first: ./scripts/start-cerid.sh"
        echo "  Proceeding anyway — workflows will work once the stack is started."
        return 1
    fi
    ok "Cerid API reachable at $CERID_API"
    return 0
}

check_macos() {
    if [[ "$(uname -s)" != "Darwin" ]]; then
        err "This script requires macOS"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Install: Finder Quick Action ("Send to Cerid KB")
# ---------------------------------------------------------------------------

install_quick_action() {
    local workflow_dir="$SERVICES_DIR/Send to Cerid KB.workflow/Contents"
    mkdir -p "$workflow_dir"

    # Info.plist — registers the workflow as a Finder Quick Action
    cat > "$workflow_dir/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSServices</key>
    <array>
        <dict>
            <key>NSMenuItem</key>
            <dict>
                <key>default</key>
                <string>Send to Cerid KB</string>
            </dict>
            <key>NSMessage</key>
            <string>runWorkflowAsService</string>
            <key>NSSendFileTypes</key>
            <array>
                <string>public.item</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
PLIST

    # document.wflow — Automator workflow definition that calls the helper script
    cat > "$workflow_dir/document.wflow" << 'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>AMApplicationBuild</key>
    <string>523</string>
    <key>AMApplicationVersion</key>
    <string>2.10</string>
    <key>AMDocumentVersion</key>
    <string>2</string>
    <key>actions</key>
    <array>
        <dict>
            <key>action</key>
            <dict>
                <key>AMAccepts</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Optional</key>
                    <false/>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.path</string>
                    </array>
                </dict>
                <key>AMActionVersion</key>
                <string>1.0.2</string>
                <key>AMApplication</key>
                <array>
                    <string>Automator</string>
                </array>
                <key>AMCategory</key>
                <string>AMCategoryUtilities</string>
                <key>AMIconName</key>
                <string>Automator</string>
                <key>AMKeywords</key>
                <array>
                    <string>Shell</string>
                    <string>Script</string>
                </array>
                <key>AMName</key>
                <string>Run Shell Script</string>
                <key>AMParameters</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <string>"$HOME/Library/Services/cerid-send.sh" "$@"</string>
                    <key>CheckedForUserDefaultShell</key>
                    <true/>
                    <key>inputMethod</key>
                    <integer>1</integer>
                    <key>shell</key>
                    <string>/bin/bash</string>
                    <key>source</key>
                    <string></string>
                </dict>
                <key>AMProvides</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.string</string>
                    </array>
                </dict>
                <key>ActionBundlePath</key>
                <string>/System/Library/Automator/Run Shell Script.action</string>
                <key>ActionName</key>
                <string>Run Shell Script</string>
                <key>ActionParameters</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <string>"$HOME/Library/Services/cerid-send.sh" "$@"</string>
                    <key>CheckedForUserDefaultShell</key>
                    <true/>
                    <key>inputMethod</key>
                    <integer>1</integer>
                    <key>shell</key>
                    <string>/bin/bash</string>
                    <key>source</key>
                    <string></string>
                </dict>
                <key>BundleIdentifier</key>
                <string>com.apple.RunShellScript</string>
                <key>CFBundleVersion</key>
                <string>1.0.2</string>
                <key>CanShowSelectedItemsWhenRun</key>
                <false/>
                <key>CanShowWhenRun</key>
                <true/>
                <key>Category</key>
                <array>
                    <string>AMCategoryUtilities</string>
                </array>
                <key>Class Name</key>
                <string>RunShellScriptAction</string>
                <key>InputUUID</key>
                <string>0</string>
                <key>Keywords</key>
                <array>
                    <string>Shell</string>
                    <string>Script</string>
                </array>
                <key>OutputUUID</key>
                <string>0</string>
                <key>UUID</key>
                <string>0</string>
                <key>UnlocalizedApplications</key>
                <array>
                    <string>Automator</string>
                </array>
                <key>arguments</key>
                <dict/>
                <key>conversionLabel</key>
                <integer>0</integer>
                <key>is498</key>
                <true/>
                <key>is498default</key>
                <true/>
                <key>isViewVisible</key>
                <true/>
                <key>location</key>
                <string>529.000000:620.000000</string>
                <key>nibPath</key>
                <string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/English.lproj/main.nib</string>
            </dict>
        </dict>
    </array>
    <key>connectors</key>
    <dict/>
    <key>workflowMetaData</key>
    <dict>
        <key>applicationBundleID</key>
        <string>com.apple.finder</string>
        <key>applicationBundleIDsByPath</key>
        <dict>
            <key>/System/Library/CoreServices/Finder.app</key>
            <string>com.apple.finder</string>
        </dict>
        <key>applicationPath</key>
        <string>/System/Library/CoreServices/Finder.app</string>
        <key>inputTypeIdentifier</key>
        <string>com.apple.Automator.fileSystemObject</string>
        <key>outputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>presentationMode</key>
        <integer>15</integer>
        <key>processesInput</key>
        <integer>0</integer>
        <key>serviceApplicationGroupName</key>
        <string>Finder</string>
        <key>serviceApplicationPath</key>
        <string>/System/Library/CoreServices/Finder.app</string>
        <key>serviceInputTypeIdentifier</key>
        <string>com.apple.Automator.fileSystemObject</string>
        <key>serviceOutputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>serviceProcessesInput</key>
        <integer>0</integer>
        <key>workflowTypeIdentifier</key>
        <string>com.apple.Automator.servicesMenu</string>
    </dict>
</dict>
</plist>
WFLOW

    # Build auth flags for curl
    local auth_flags=""
    if [ -n "$CERID_API_KEY_VAL" ]; then
        auth_flags="-H \"X-API-Key: ${CERID_API_KEY_VAL}\""
    elif [ -n "$CERID_SECRET_VAL" ]; then
        auth_flags="-H \"X-Webhook-Secret: ${CERID_SECRET_VAL}\""
    fi

    # Helper script called by the workflow
    cat > "$SERVICES_DIR/cerid-send.sh" << SCRIPT
#!/usr/bin/env bash
# Cerid AI - Finder Quick Action helper
# Reads file content and POSTs to Cerid webhook

CERID_API="${CERID_API}"

for f in "\$@"; do
    if [ ! -r "\$f" ]; then
        continue
    fi
    content=\$(head -c 50000 "\$f" 2>/dev/null || true)
    if [ -z "\$content" ]; then
        osascript -e "display notification \"Skipped empty file: \$(basename "\$f")\" with title \"Cerid AI\"" 2>/dev/null || true
        continue
    fi
    title=\$(basename "\$f")
    json_content=\$(printf '%s' "\$content" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
    if curl -sf --max-time 30 -X POST "\${CERID_API}/ingest/webhook" \\
        -H "Content-Type: application/json" \\
        ${auth_flags} \\
        -d "{\"text\": \${json_content}, \"source\": \"finder\", \"title\": \"\$title\"}" \\
        >/dev/null 2>&1; then
        osascript -e "display notification \"Ingested: \$title\" with title \"Cerid AI\"" 2>/dev/null || true
    else
        osascript -e "display notification \"Failed: \$title\" with title \"Cerid AI\" subtitle \"Check that Cerid is running\"" 2>/dev/null || true
    fi
done
SCRIPT
    chmod +x "$SERVICES_DIR/cerid-send.sh"

    ok "Finder Quick Action 'Send to Cerid KB' installed"
    echo "  Right-click files in Finder > Quick Actions > Send to Cerid KB"
}

# ---------------------------------------------------------------------------
# Install: Services menu item for selected text
# ---------------------------------------------------------------------------

install_services_menu() {
    local workflow_dir="$SERVICES_DIR/Ingest to Cerid.workflow/Contents"
    mkdir -p "$workflow_dir"

    # Info.plist — registers as a text service
    cat > "$workflow_dir/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSServices</key>
    <array>
        <dict>
            <key>NSMenuItem</key>
            <dict>
                <key>default</key>
                <string>Ingest to Cerid</string>
            </dict>
            <key>NSMessage</key>
            <string>runWorkflowAsService</string>
            <key>NSSendTypes</key>
            <array>
                <string>NSStringPboardType</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
PLIST

    # document.wflow — pipes stdin text to the helper script
    cat > "$workflow_dir/document.wflow" << 'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>AMApplicationBuild</key>
    <string>523</string>
    <key>AMApplicationVersion</key>
    <string>2.10</string>
    <key>AMDocumentVersion</key>
    <string>2</string>
    <key>actions</key>
    <array>
        <dict>
            <key>action</key>
            <dict>
                <key>AMAccepts</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Optional</key>
                    <false/>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.string</string>
                    </array>
                </dict>
                <key>AMActionVersion</key>
                <string>1.0.2</string>
                <key>AMApplication</key>
                <array>
                    <string>Automator</string>
                </array>
                <key>AMCategory</key>
                <string>AMCategoryUtilities</string>
                <key>AMIconName</key>
                <string>Automator</string>
                <key>AMName</key>
                <string>Run Shell Script</string>
                <key>AMParameters</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <string>"$HOME/Library/Services/cerid-text.sh"</string>
                    <key>CheckedForUserDefaultShell</key>
                    <true/>
                    <key>inputMethod</key>
                    <integer>0</integer>
                    <key>shell</key>
                    <string>/bin/bash</string>
                    <key>source</key>
                    <string></string>
                </dict>
                <key>AMProvides</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.string</string>
                    </array>
                </dict>
                <key>ActionBundlePath</key>
                <string>/System/Library/Automator/Run Shell Script.action</string>
                <key>ActionName</key>
                <string>Run Shell Script</string>
                <key>ActionParameters</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <string>"$HOME/Library/Services/cerid-text.sh"</string>
                    <key>CheckedForUserDefaultShell</key>
                    <true/>
                    <key>inputMethod</key>
                    <integer>0</integer>
                    <key>shell</key>
                    <string>/bin/bash</string>
                    <key>source</key>
                    <string></string>
                </dict>
                <key>BundleIdentifier</key>
                <string>com.apple.RunShellScript</string>
                <key>CFBundleVersion</key>
                <string>1.0.2</string>
                <key>CanShowSelectedItemsWhenRun</key>
                <false/>
                <key>CanShowWhenRun</key>
                <true/>
                <key>Class Name</key>
                <string>RunShellScriptAction</string>
                <key>InputUUID</key>
                <string>0</string>
                <key>OutputUUID</key>
                <string>0</string>
                <key>UUID</key>
                <string>0</string>
                <key>arguments</key>
                <dict/>
                <key>isViewVisible</key>
                <true/>
            </dict>
        </dict>
    </array>
    <key>connectors</key>
    <dict/>
    <key>workflowMetaData</key>
    <dict>
        <key>inputTypeIdentifier</key>
        <string>com.apple.Automator.text</string>
        <key>outputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>presentationMode</key>
        <integer>15</integer>
        <key>processesInput</key>
        <integer>0</integer>
        <key>serviceInputTypeIdentifier</key>
        <string>com.apple.Automator.text</string>
        <key>serviceOutputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>serviceProcessesInput</key>
        <integer>0</integer>
        <key>workflowTypeIdentifier</key>
        <string>com.apple.Automator.servicesMenu</string>
    </dict>
</dict>
</plist>
WFLOW

    # Build auth flags for curl
    local auth_flags=""
    if [ -n "$CERID_API_KEY_VAL" ]; then
        auth_flags="-H \"X-API-Key: ${CERID_API_KEY_VAL}\""
    elif [ -n "$CERID_SECRET_VAL" ]; then
        auth_flags="-H \"X-Webhook-Secret: ${CERID_SECRET_VAL}\""
    fi

    # Helper script: reads stdin text and POSTs to Cerid
    cat > "$SERVICES_DIR/cerid-text.sh" << SCRIPT
#!/usr/bin/env bash
# Cerid AI - Services menu helper
# Reads selected text from stdin and POSTs to Cerid webhook

CERID_API="${CERID_API}"

text=\$(cat)
if [ -z "\$text" ] || [ \${#text} -lt 10 ]; then
    osascript -e 'display notification "No text selected or too short" with title "Cerid AI"' 2>/dev/null || true
    exit 0
fi

# Truncate to 50KB
text=\$(printf '%s' "\$text" | head -c 50000)
json_text=\$(printf '%s' "\$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')

if curl -sf --max-time 30 -X POST "\${CERID_API}/ingest/webhook" \\
    -H "Content-Type: application/json" \\
    ${auth_flags} \\
    -d "{\"text\": \${json_text}, \"source\": \"services-menu\"}" \\
    >/dev/null 2>&1; then
    char_count=\${#text}
    osascript -e "display notification \"Ingested \$char_count chars\" with title \"Cerid AI\"" 2>/dev/null || true
else
    osascript -e 'display notification "Ingestion failed — check that Cerid is running" with title "Cerid AI" subtitle "Error"' 2>/dev/null || true
fi
SCRIPT
    chmod +x "$SERVICES_DIR/cerid-text.sh"

    ok "Services menu item 'Ingest to Cerid' installed"
    echo "  Select text in any app > right-click > Services > Ingest to Cerid"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
    rm -rf "$SERVICES_DIR/Send to Cerid KB.workflow" 2>/dev/null || true
    rm -rf "$SERVICES_DIR/Ingest to Cerid.workflow" 2>/dev/null || true
    rm -f "$SERVICES_DIR/cerid-send.sh" 2>/dev/null || true
    rm -f "$SERVICES_DIR/cerid-text.sh" 2>/dev/null || true
    ok "All Cerid macOS integrations removed"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check_macos

case "${1:-}" in
    --quick-action)
        check_cerid_running
        install_quick_action
        ;;
    --services)
        check_cerid_running
        install_services_menu
        ;;
    --uninstall)
        uninstall
        ;;
    --all|"")
        check_cerid_running
        install_quick_action
        install_services_menu
        /System/Library/CoreServices/pbs -flush 2>/dev/null || true
        echo ""
        ok "All Cerid macOS integrations installed"
        echo ""
        echo "Usage:"
        echo "  - Quick Action: Right-click files in Finder > Quick Actions > Send to Cerid KB"
        echo "  - Service: Select text in any app > right-click > Services > Ingest to Cerid"
        echo ""
        echo "Note: You may need to enable these in System Settings > Keyboard > Keyboard Shortcuts > Services"
        ;;
    *)
        echo "Usage: $0 [--all|--quick-action|--services|--uninstall]"
        exit 1
        ;;
esac
