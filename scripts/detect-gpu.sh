#!/bin/bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

# Cerid AI — GPU / Compute Detection
#
# Detects available GPU hardware for Ollama model selection and recommends
# the right local inference backend for the host.
#
# Exports: CERID_GPU_TYPE (nvidia|amd|amd-mac|metal|cpu)
#          CERID_GPU_VRAM_MB (integer, 0 for CPU-only)
#          CERID_OLLAMA_IMAGE (ollama/ollama or ollama/ollama:rocm or native)
#          CERID_GPU_LABEL (human-readable summary)
#          CERID_RECOMMENDED_LOCAL_BACKEND (ollama|quenchforge|cloud)
#
# About "amd-mac": Intel Mac with AMD discrete GPU. Stock Ollama on this
# hardware falls back to CPU (ollama/ollama#1016, open since 2023). The
# Quenchforge service (cerid-ai/quenchforge, Apache-2.0) carries patched
# llama.cpp that actually drives the AMD GPU via Metal. Recommendation
# defaults to quenchforge for this case.
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
    local recommended_backend="cloud"

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
            recommended_backend="ollama"
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
            recommended_backend="ollama"
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
            recommended_backend="ollama"
        else
            # Intel Mac with possible discrete GPU
            local dgpu
            dgpu=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "Chipset Model" | head -1 | sed 's/.*: //' || echo "")
            if [ -n "$dgpu" ] && echo "$dgpu" | grep -qiE "AMD|Radeon|Vega"; then
                # Intel Mac + AMD discrete GPU — mainline ollama can't
                # drive Metal on this hardware (ollama/ollama#1016, open
                # since 2023). Quenchforge carries the load-bearing
                # macOS-only llama.cpp patches that work around it —
                # recommend quenchforge here. Fall back to ollama remains
                # available via INTERNAL_LLM_PROVIDER=ollama for operators
                # who want to opt out of quenchforge.
                gpu_type="amd-mac"
                local vram_str
                vram_str=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "VRAM" | head -1 | grep -oE '[0-9]+' || echo "0")
                vram_mb=$((vram_str * 1024))  # Usually reported in GB
                gpu_label="$dgpu (${vram_mb}MB VRAM) — recommend Quenchforge"
                ollama_image="native"
                recommended_backend="quenchforge"
            elif [ -n "$dgpu" ] && ! echo "$dgpu" | grep -qi "Intel"; then
                # Intel Mac + non-AMD non-Intel discrete (e.g. NVIDIA in
                # an older Mac). macOS dropped NVIDIA drivers after Mojave,
                # so this is effectively rare; treat as best-effort Metal.
                gpu_type="metal"
                local vram_str
                vram_str=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "VRAM" | head -1 | grep -oE '[0-9]+' || echo "0")
                vram_mb=$((vram_str * 1024))
                gpu_label="$dgpu (${vram_mb}MB VRAM)"
                ollama_image="native"
                recommended_backend="ollama"
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
        # CPU-only: recommend cloud routing for serious workloads
        recommended_backend="cloud"
    fi

    # Export for consumption by start-cerid.sh and the setup wizard
    export CERID_GPU_TYPE="$gpu_type"
    export CERID_GPU_VRAM_MB="$vram_mb"
    export CERID_OLLAMA_IMAGE="$ollama_image"
    export CERID_GPU_LABEL="$gpu_label"
    export CERID_RECOMMENDED_LOCAL_BACKEND="$recommended_backend"
}

detect_gpu

# If run directly (not sourced), print results
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "GPU Type:        $CERID_GPU_TYPE"
    echo "VRAM/Memory:     ${CERID_GPU_VRAM_MB}MB"
    echo "Ollama Image:    $CERID_OLLAMA_IMAGE"
    echo "Summary:         $CERID_GPU_LABEL"
    echo "Recommended:     $CERID_RECOMMENDED_LOCAL_BACKEND"
fi
