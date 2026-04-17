#!/bin/sh
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# setup-archive.sh — create the standard ~/cerid-archive domain folders.
#
# Replaces the README's prior `mkdir -p ~/cerid-archive/{coding,finance,...}`
# one-liner. That brace-expansion syntax works on bash/zsh but breaks on
# POSIX sh / dash, silently creating one directory literally named
# "{coding,finance,projects,personal,general,inbox}". This script iterates
# explicitly so every supported shell behaves the same.
#
# Usage:
#   ./scripts/setup-archive.sh
#   ARCHIVE_ROOT=/path/to/custom ./scripts/setup-archive.sh
#
# Idempotent — safe to re-run.

set -eu

ARCHIVE_ROOT="${ARCHIVE_ROOT:-$HOME/cerid-archive}"
DOMAINS="coding finance projects personal general inbox"

if [ -e "$ARCHIVE_ROOT" ] && [ ! -d "$ARCHIVE_ROOT" ]; then
    echo "Error: $ARCHIVE_ROOT exists but is not a directory." >&2
    exit 1
fi

mkdir -p "$ARCHIVE_ROOT"

created=0
skipped=0
for domain in $DOMAINS; do
    if [ -d "$ARCHIVE_ROOT/$domain" ]; then
        skipped=$((skipped + 1))
    else
        mkdir -p "$ARCHIVE_ROOT/$domain"
        created=$((created + 1))
    fi
done

echo "Archive ready at $ARCHIVE_ROOT"
echo "  Created: $created  Already existed: $skipped  Total: $(printf '%s\n' $DOMAINS | wc -l | tr -d ' ')"
