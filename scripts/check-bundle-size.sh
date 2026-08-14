#!/usr/bin/env bash
# Frontend bundle-size caps. Mirrors the ci.yml `frontend` job step, extracted so
# CI and `make prepush` run ONE definition rather than two copies that drift.
#
# Run from the repo root; expects src/web/dist to exist (npx vite build).
set -euo pipefail
cd "$(dirname "$0")/../src/web"

if [ ! -d dist/assets ]; then
  echo "ERROR: src/web/dist/assets missing — run 'npx vite build' first." >&2
  exit 1
fi

main_max=$((800 * 1024))
lazy_max=$((1300 * 1024))
fail=0

for file in dist/assets/*.js; do
  [ -e "$file" ] || continue
  size=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null)
  size_kb=$((size / 1024))
  base=$(basename "$file")
  cap=$main_max
  cap_name="800KB"
  # Route-split chunks, never in the initial payload: the 2D constellation map
  # and the cosmos.gl "Live" scene. vendor-r3f and vendor-three were dropped
  # with the 3D render stack on 2026-08-13 — a cap naming chunks that can no
  # longer be emitted checks nothing.
  case "$base" in
    Constellation-*|vendor-cosmos-*)
      cap=$lazy_max
      cap_name="1.3MB (lazy route chunk)" ;;
  esac
  echo "  $file: ${size_kb}KB (cap ${cap_name})"
  if [ "$size" -gt "$cap" ]; then
    echo "::error::Bundle chunk $file exceeds $cap_name limit: ${size_kb}KB"
    fail=1
  fi
done

[ "$fail" -eq 1 ] && exit 1
echo "✓ all chunks within caps"
