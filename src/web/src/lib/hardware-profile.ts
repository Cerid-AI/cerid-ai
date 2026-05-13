// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Hardware-profile helpers for the setup wizard's Backend Recommendation step.
 *
 * The authoritative recommendation comes from the server side
 * (``/system-check`` returning ``recommended_local_backend``). These helpers
 * supply user-facing copy and a deterministic client-side fallback in case
 * the API surfaces an empty value.
 */

import type {
  GpuType,
  RecommendedLocalBackend,
  SystemCheckResponse,
} from "./types"

export interface BackendOption {
  /** Stable identifier sent to the backend as ``INTERNAL_LLM_PROVIDER``. */
  id: RecommendedLocalBackend
  /** Display label rendered in the wizard. */
  label: string
  /** Short subtitle explaining the trade-off in one sentence. */
  blurb: string
  /** Optional badge string ("Recommended", "Detected", etc). */
  badge?: string
}

/**
 * Compute the three backend options to display, with the recommended one
 * pre-selected and badged. The order is stable and not driven by recommendation
 * so users can scan them consistently.
 */
export function backendOptionsForHardware(
  sys: SystemCheckResponse | null,
): { options: BackendOption[]; defaultId: RecommendedLocalBackend } {
  const defaultId: RecommendedLocalBackend =
    sys?.recommended_local_backend ?? deriveRecommendation(sys)

  const options: BackendOption[] = [
    {
      id: "ollama",
      label: "Ollama (Local)",
      blurb:
        "Runs models locally via Ollama. Works on Apple Silicon, NVIDIA Linux/Windows, and AMD Linux with full GPU acceleration.",
    },
    {
      id: "quenchforge",
      label: "Quenchforge (Local, Mac + AMD)",
      blurb:
        "Local inference for Intel Mac + AMD discrete GPU where stock Ollama falls back to CPU. Apache-2.0, separate install.",
    },
    {
      id: "cloud",
      label: "Cloud (OpenRouter)",
      blurb:
        "Routes inference through OpenRouter (or another configured cloud provider). No local model download required.",
    },
  ]

  for (const opt of options) {
    if (opt.id === defaultId) {
      opt.badge = "Recommended"
    } else if (sys?.ollama_detected && opt.id === "ollama") {
      opt.badge = "Detected"
    }
  }

  return { options, defaultId }
}

/**
 * Client-side fallback recommendation when ``recommended_local_backend`` is
 * absent from the API response. Mirrors the truth-table in
 * ``src/mcp/app/routers/setup.py:_recommend_backend_from_hw`` and
 * ``scripts/detect-gpu.sh``, so the wizard renders a sensible default even
 * when ``HOST_*`` env vars never propagated to the container.
 */
export function deriveRecommendation(
  sys: SystemCheckResponse | null,
): RecommendedLocalBackend {
  if (!sys) return "cloud"

  const gpuType: GpuType = (sys.gpu_type ?? "") as GpuType
  if (gpuType === "amd-mac") return "quenchforge"

  // String-level fallback: Intel Mac + AMD discrete (not iGPU)
  const os = (sys.os ?? "").toLowerCase()
  const gpu = (sys.gpu ?? "").toLowerCase()
  if (
    os.includes("mac") &&
    (gpu.includes("amd") || gpu.includes("radeon")) &&
    !gpu.includes("iris") &&
    !gpu.includes("intel hd") &&
    !gpu.includes("apple")
  ) {
    return "quenchforge"
  }

  const accel = (sys.gpu_acceleration ?? "").toLowerCase()
  if (accel === "cuda" || accel === "rocm" || accel === "metal") return "ollama"
  if (sys.ollama_detected) return "ollama"
  return "cloud"
}

/**
 * Maps a backend id to the Settings → Inference Backend section copy.
 * Used by the header pill (label only) and the Settings tab (label + blurb).
 */
export function backendSummary(
  id: RecommendedLocalBackend,
): { label: string; tone: "local" | "cloud" } {
  switch (id) {
    case "quenchforge":
      return { label: "Quenchforge", tone: "local" }
    case "ollama":
      return { label: "Ollama", tone: "local" }
    case "cloud":
      return { label: "Cloud", tone: "cloud" }
  }
}
