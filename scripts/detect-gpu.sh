#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI — GPU / Compute Detection
#
# Detects available GPU hardware for Ollama model selection.
# Exports: CERID_GPU_TYPE (nvidia|amd|metal|cpu)
#          CERID_GPU_VRAM_MB (integer, 0 for CPU-only)
#          CERID_OLLAMA_IMAGE (ollama/ollama or ollama/ollama:rocm)
#          CERID_GPU_LABEL (human-readable summary)
#
# Usage:
#   source scripts/detect-gpu.sh        # sets env vars in current shell
#   ./scripts/detect-gpu.sh             # prints detection results

set -euo pipefail

detect_gpu() {
    local gpu_type="cpu"
    local vram_mb=0
    local ollama_image="ollama/ollama:latest"
    local gpu_label=""

    # --- NVIDIA (Linux/Windows WSL) ---
    if command -v nvidia-smi &>/dev/null; then
        local nvidia_output
        nvidia_output=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null || echo "")
        if [ -n "$nvidia_output" ]; then
            gpu_type="nvidia"
            # Parse VRAM from first GPU (format: "GeForce RTX 4090, 24564")
            vram_mb=$(echo "$nvidia_output" | head -1 | awk -F', ' '{print $2}' | tr -d ' ')
            local gpu_name
            gpu_name=$(echo "$nvidia_output" | head -1 | awk -F', ' '{print $1}')
            gpu_label="NVIDIA $gpu_name (${vram_mb}MB VRAM)"
            ollama_image="ollama/ollama:latest"
        fi
    fi

    # --- AMD ROCm (Linux) ---
    if [ "$gpu_type" = "cpu" ] && command -v rocm-smi &>/dev/null; then
        local rocm_output
        rocm_output=$(rocm-smi --showmeminfo vram 2>/dev/null || echo "")
        if [ -n "$rocm_output" ]; then
            gpu_type="amd"
            # Parse total VRAM (bytes → MB)
            local vram_bytes
            vram_bytes=$(echo "$rocm_output" | grep "Total" | head -1 | awk '{print $NF}' || echo "0")
            vram_mb=$((vram_bytes / 1024 / 1024))
            gpu_label="AMD ROCm GPU (${vram_mb}MB VRAM)"
            ollama_image="ollama/ollama:rocm"
        fi
    fi

    # --- macOS Metal (Apple Silicon / discrete GPU) ---
    if [ "$gpu_type" = "cpu" ] && [[ "$OSTYPE" == "darwin"* ]]; then
        # Apple Silicon has unified memory — report total RAM as available
        if sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi "apple"; then
            gpu_type="metal"
            local total_ram_bytes
            total_ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "0")
            # Ollama on macOS Metal uses unified memory — report ~75% as available
            vram_mb=$(( (total_ram_bytes / 1024 / 1024) * 3 / 4 ))
            local chip
            chip=$(sysctl -n machdep.cpu.brand_string 2>/dev/null | sed 's/Apple //')
            gpu_label="Apple $chip Metal (${vram_mb}MB unified memory)"
            # macOS: Ollama runs natively (not in Docker) for Metal acceleration
            ollama_image="native"
        else
            # Intel Mac with possible discrete GPU
            local dgpu
            dgpu=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "Chipset Model" | head -1 | sed 's/.*: //' || echo "")
            if [ -n "$dgpu" ] && ! echo "$dgpu" | grep -qi "Intel"; then
                gpu_type="metal"
                # Discrete GPU VRAM
                local vram_str
                vram_str=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "VRAM" | head -1 | grep -oE '[0-9]+' || echo "0")
                vram_mb=$((vram_str * 1024))  # Usually reported in GB
                gpu_label="$dgpu (${vram_mb}MB VRAM)"
                ollama_image="native"
            fi
        fi
    fi

    # --- CPU fallback ---
    if [ "$gpu_type" = "cpu" ]; then
        local ram_mb=0
        if [[ "$OSTYPE" == "darwin"* ]]; then
            local ram_bytes
            ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "0")
            ram_mb=$((ram_bytes / 1024 / 1024))
        elif [ -f /proc/meminfo ]; then
            local ram_kb
            ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
            ram_mb=$((ram_kb / 1024))
        fi
        vram_mb=0
        gpu_label="CPU only (${ram_mb}MB system RAM)"
        ollama_image="ollama/ollama:latest"
    fi

    # Export for consumption by start-cerid.sh
    export CERID_GPU_TYPE="$gpu_type"
    export CERID_GPU_VRAM_MB="$vram_mb"
    export CERID_OLLAMA_IMAGE="$ollama_image"
    export CERID_GPU_LABEL="$gpu_label"
}

detect_gpu

# If run directly (not sourced), print results
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "GPU Type:      $CERID_GPU_TYPE"
    echo "VRAM/Memory:   ${CERID_GPU_VRAM_MB}MB"
    echo "Ollama Image:  $CERID_OLLAMA_IMAGE"
    echo "Summary:       $CERID_GPU_LABEL"
fi
