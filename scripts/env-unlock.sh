#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Decrypt .env.age → .env using age key from dotfiles.
# Key location: ~/.config/cerid/age-key.txt (override with CERID_AGE_KEY)

set -euo pipefail
cd "$(dirname "$0")/.."

KEY="${CERID_AGE_KEY:-$HOME/.config/cerid/age-key.txt}"

if [ ! -f .env.age ]; then
    echo "Error: .env.age not found. Nothing to decrypt."
    exit 1
fi

if [ ! -f "$KEY" ]; then
    echo "Error: Age key not found at $KEY"
    echo "Generate one with: age-keygen -o ~/.config/cerid/age-key.txt"
    exit 1
fi

age -d -i "$KEY" -o .env .env.age
echo "Unlocked .env.age → .env"