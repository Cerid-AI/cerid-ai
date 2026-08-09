#!/bin/bash
# Separate the marketing site into its own repository.
# Run from the cerid-ai repo root.
# Usage: ./scripts/separate-marketing.sh /path/to/new/repo

set -euo pipefail

DEST="${1:?Usage: $0 /path/to/new/cerid-ai-marketing}"

if [ ! -d "packages/marketing" ]; then
  echo "Error: packages/marketing/ not found. Run this from the cerid-ai repo root."
  exit 1
fi

echo "Copying marketing site to $DEST..."
mkdir -p "$DEST"
cp -r packages/marketing/* "$DEST/"
cp -r packages/marketing/.* "$DEST/" 2>/dev/null || true

# Remove build artifacts and node_modules from the copy
rm -rf "$DEST/.next" "$DEST/node_modules"

cd "$DEST"
echo "Initializing git..."
git init
git add .
git commit -m "Initial commit: marketing site separated from cerid-ai monorepo"

echo ""
echo "Done. Next steps:"
echo "  1. cd $DEST"
echo "  2. Verify: npm install && npm run dev"
echo "  3. Create repo: gh repo create Cerid-AI/cerid-ai-marketing --public --source=."
echo "  4. Push: git push -u origin main"
echo "  5. Configure Vercel to deploy from the new repo"
echo "  6. Remove packages/marketing/ from cerid-ai and update CI"
