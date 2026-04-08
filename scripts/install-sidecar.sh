#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI — Sidecar Install Script
# Detects platform and installs the FastEmbed sidecar with appropriate GPU support.
#
# Usage:
#   bash scripts/install-sidecar.sh
#
# After install:
#   python scripts/cerid-sidecar.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Cerid AI Sidecar Installer ==="
echo ""

# Detect platform
OS="$(uname -s)"
ARCH="$(uname -m)"

echo "Platform: $OS $ARCH"

# Base dependencies
DEPS="fastapi uvicorn httpx huggingface-hub tokenizers onnxruntime numpy"

case "$OS" in
    Darwin)
        if [ "$ARCH" = "arm64" ]; then
            echo "Detected: macOS Apple Silicon (Metal support)"
            # onnxruntime includes CoreML on macOS ARM
            DEPS="$DEPS"
            echo ""
            echo "Note: CoreML execution provider is included in onnxruntime on Apple Silicon."
        else
            echo "Detected: macOS Intel (CPU only)"
        fi
        ;;
    Linux)
        if command -v nvidia-smi &>/dev/null; then
            echo "Detected: Linux with NVIDIA GPU"
            # Replace onnxruntime with GPU version
            DEPS="${DEPS/onnxruntime/onnxruntime-gpu}"
            echo "Installing onnxruntime-gpu for CUDA support."
        elif command -v rocm-smi &>/dev/null; then
            echo "Detected: Linux with AMD ROCm GPU"
            DEPS="${DEPS/onnxruntime/onnxruntime-rocm}"
            echo "Installing onnxruntime-rocm for ROCm support."
        else
            echo "Detected: Linux CPU only"
        fi
        ;;
    *)
        echo "Detected: $OS (CPU only)"
        ;;
esac

echo ""
echo "Installing: $DEPS"
echo ""

# Use pip from the current Python
pip install --quiet $DEPS

echo ""
echo "=== Installation complete ==="
echo ""
echo "Start the sidecar:"
echo "  python $CERID_ROOT/scripts/cerid-sidecar.py"
echo ""
echo "Or with a custom port:"
echo "  CERID_SIDECAR_PORT=8890 python $CERID_ROOT/scripts/cerid-sidecar.py"
echo ""
echo "The sidecar runs OUTSIDE Docker to access your GPU directly."
echo "Cerid AI will auto-detect it at startup."
