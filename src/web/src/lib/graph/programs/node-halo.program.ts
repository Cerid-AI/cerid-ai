// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Custom sigma.js v3 NodeProgram that renders a soft halo ring around
// each node. Layered behind sigma's built-in NodeCircleProgram via
// createNodeCompoundProgram (see graphology-adapter.ts > Atlas.tsx wiring).
//
// Visual encoding (design-system-v2 §3 + viz-spec §2.2):
//   - Halo color sourced from per-node `haloColor` attribute (trust state)
//   - Halo radius extends ~50% past disc radius
//   - Halo opacity modulated by per-node `pulseIntensity` (0-1, derived
//     from recency_score; v1 is static intensity, not time-animated —
//     animated pulse arrives in Day 6+ if perf budget allows)
//
// Structurally mirrors sigma's bundled NodeCircleProgram: 3 vertices per
// node, TRIANGLES method, same angle constants. The vertex shader expands
// each triangle further than NodeCircleProgram (size × 6 vs × 4) to give
// the fragment shader enough surface area to carve out a halo ring.

import { NodeProgram } from "sigma/rendering"
import type { ProgramInfo } from "sigma/rendering"
import type { NodeDisplayData, RenderParams } from "sigma/types"

// ---------------------------------------------------------------------------
// Vertex shader
// ---------------------------------------------------------------------------

const VERTEX_SHADER_SOURCE = /*glsl*/ `
attribute vec4 a_id;
attribute vec4 a_haloColor;
attribute vec2 a_position;
attribute float a_size;
attribute float a_intensity;
attribute float a_angle;

uniform mat3 u_matrix;
uniform float u_sizeRatio;
uniform float u_correctionRatio;

varying vec4 v_haloColor;
varying vec2 v_diffVector;
varying float v_radius;
varying float v_intensity;

const float bias = 255.0 / 254.0;
// Halo extends to 1.5x the disc radius. The triangle must enclose the
// halo, so we expand by 6.0 (matching NodeCircleProgram's 4.0 × 1.5).
const float HALO_EXPAND = 6.0;

void main() {
  float size = a_size * u_correctionRatio / u_sizeRatio * HALO_EXPAND;
  vec2 diffVector = size * vec2(cos(a_angle), sin(a_angle));
  vec2 position = a_position + diffVector;
  gl_Position = vec4(
    (u_matrix * vec3(position, 1)).xy,
    0,
    1
  );

  v_diffVector = diffVector;
  v_radius = size / 2.0;
  v_intensity = a_intensity;

  #ifdef PICKING_MODE
  // Halo is not pickable — picking goes through NodeCircleProgram disc.
  v_haloColor = a_id;
  #else
  v_haloColor = a_haloColor;
  #endif

  v_haloColor.a *= bias;
}
`

// ---------------------------------------------------------------------------
// Fragment shader — SDF ring
// ---------------------------------------------------------------------------

const FRAGMENT_SHADER_SOURCE = /*glsl*/ `
precision highp float;

varying vec4 v_haloColor;
varying vec2 v_diffVector;
varying float v_radius;
varying float v_intensity;

uniform float u_correctionRatio;

const vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);

void main(void) {
  float border = u_correctionRatio * 2.0;
  float dist = length(v_diffVector);

  // Halo lives between (radius * 0.66) and (radius * 1.0); inside the
  // inner edge we draw nothing so the disc shows through cleanly.
  float discRadius = v_radius * 0.66;
  float outerRadius = v_radius;

  #ifdef PICKING_MODE
  // Halo is non-pickable
  gl_FragColor = transparent;
  #else

  float alpha = 0.0;
  if (dist < discRadius - border) {
    alpha = 0.0;
  } else if (dist < discRadius) {
    // Soft inner edge — fades in over the border width
    alpha = (dist - (discRadius - border)) / border;
  } else if (dist < outerRadius - border) {
    // Solid halo ring
    alpha = 1.0;
  } else if (dist < outerRadius) {
    // Soft outer edge — fades out over the border width
    alpha = 1.0 - (dist - (outerRadius - border)) / border;
  } else {
    alpha = 0.0;
  }

  // Modulate halo brightness by per-node intensity (recency + focused)
  alpha *= clamp(v_intensity, 0.0, 1.0);

  gl_FragColor = vec4(v_haloColor.rgb, v_haloColor.a * alpha);
  #endif
}
`

// ---------------------------------------------------------------------------
// Program
// ---------------------------------------------------------------------------

const UNIFORMS = ["u_sizeRatio", "u_correctionRatio", "u_matrix"] as const
type UniformName = (typeof UNIFORMS)[number]

const ANGLE_1 = 0
const ANGLE_2 = (2 * Math.PI) / 3
const ANGLE_3 = (4 * Math.PI) / 3

/**
 * Pack a hex color "#RRGGBB" into a single Float32 (sigma's bytes-as-float
 * encoding: 4 unsigned bytes packed into 32 bits, normalized in shader).
 */
function packColorToFloat(hex: string | undefined, fallbackAlpha = 1): number {
  if (!hex) return 0
  const cleaned = hex.replace("#", "")
  const r = parseInt(cleaned.slice(0, 2), 16) || 0
  const g = parseInt(cleaned.slice(2, 4), 16) || 0
  const b = parseInt(cleaned.slice(4, 6), 16) || 0
  const a = Math.round(fallbackAlpha * 255)
  const packed = (a << 24) | (b << 16) | (g << 8) | r
  // Reinterpret 32-bit uint as Float32 — sigma does this via Float32Array
  // backed by a shared Uint32Array view, but a single-element conversion
  // suffices here.
  const buf = new ArrayBuffer(4)
  new Uint32Array(buf)[0] = packed >>> 0
  return new Float32Array(buf)[0]
}

/**
 * Extended node attributes that the adapter populates with halo + intensity
 * data. Used to type-narrow what `processVisibleItem` reads off `data`.
 */
interface NodeDisplayDataWithHalo extends NodeDisplayData {
  haloColor?: string
  pulseIntensity?: number
  focused?: boolean
  recency_score?: number
}

export default class NodeHaloProgram extends NodeProgram<UniformName> {
  static readonly ANGLE_1 = ANGLE_1
  static readonly ANGLE_2 = ANGLE_2
  static readonly ANGLE_3 = ANGLE_3

  getDefinition() {
    return {
      VERTICES: 3,
      VERTEX_SHADER_SOURCE,
      FRAGMENT_SHADER_SOURCE,
      METHOD: WebGLRenderingContext.TRIANGLES,
      UNIFORMS,
      ATTRIBUTES: [
        { name: "a_position", size: 2, type: WebGLRenderingContext.FLOAT },
        { name: "a_size", size: 1, type: WebGLRenderingContext.FLOAT },
        { name: "a_haloColor", size: 4, type: WebGLRenderingContext.UNSIGNED_BYTE, normalized: true },
        { name: "a_intensity", size: 1, type: WebGLRenderingContext.FLOAT },
        { name: "a_id", size: 4, type: WebGLRenderingContext.UNSIGNED_BYTE, normalized: true },
      ],
      CONSTANT_ATTRIBUTES: [
        { name: "a_angle", size: 1, type: WebGLRenderingContext.FLOAT },
      ],
      CONSTANT_DATA: [[ANGLE_1], [ANGLE_2], [ANGLE_3]],
    }
  }

  processVisibleItem(nodeIndex: number, startIndex: number, data: NodeDisplayData): void {
    const array = this.array
    const halo = data as NodeDisplayDataWithHalo

    // Position
    array[startIndex++] = data.x
    array[startIndex++] = data.y
    // Size (halo wraps disc — same source size; expand happens in vertex shader)
    array[startIndex++] = data.size
    // Halo color (packed RGBA → single Float32)
    array[startIndex++] = packColorToFloat(halo.haloColor, 0.55)
    // Intensity: pulseIntensity if present, otherwise derive from recency
    let intensity = halo.pulseIntensity
    if (intensity === undefined) {
      intensity = halo.recency_score ?? 0.5
    }
    if (halo.focused) intensity = Math.min(1, intensity * 1.4)
    array[startIndex++] = intensity
    // Picking ID
    array[startIndex++] = nodeIndex
  }

  setUniforms(params: RenderParams, { gl, uniformLocations }: ProgramInfo): void {
    const { u_sizeRatio, u_correctionRatio, u_matrix } = uniformLocations
    gl.uniform1f(u_correctionRatio, params.correctionRatio)
    gl.uniform1f(u_sizeRatio, params.sizeRatio)
    gl.uniformMatrix3fv(u_matrix, false, params.matrix)
  }
}
