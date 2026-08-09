#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Dry-run publish inspection.
# Packs the widget into a local tarball and lists its contents.
# Does NOT publish to npm.
#
# Usage: ./scripts/publish-dry.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PACKAGE_DIR}"

echo "==> Building package..."
npm run build

echo ""
echo "==> Packing tarball (dry run)..."
npm pack --dry-run 2>&1

echo ""
echo "==> Creating tarball for inspection..."
TARBALL=$(npm pack 2>/dev/null | tail -1)
echo "Tarball: ${TARBALL}"

echo ""
echo "==> Contents:"
tar -tzvf "${TARBALL}"

echo ""
echo "==> Size:"
du -sh "${TARBALL}"

# Clean up the tarball — don't leave it around
rm -f "${TARBALL}"

echo ""
echo "Done. (Tarball deleted — this was a dry run.)"
echo "To actually publish: npm publish --access public"
