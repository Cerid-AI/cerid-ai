#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Encrypt .env → .env.age using the cerid age public key.
# Safe to run anytime — overwrites previous .env.age.

set -euo pipefail
cd "$(dirname "$0")/.."

PUBKEY="age12q026nqknm8l97s87hv4qmtxs60jg6dcf95ukf5en53ucc236gzswxj6ky"

if [ ! -f .env ]; then
    echo "Error: .env not found at repo root. Nothing to encrypt."
    exit 1
fi

age -r "$PUBKEY" -o .env.age .env
echo "Locked .env → .env.age"