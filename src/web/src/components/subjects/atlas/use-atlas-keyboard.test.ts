// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for useAtlasKeyboard. Verifies key dispatch + selection
// cycling against a controlled graphology graph + stub sigma instance.

import { describe, expect, it, vi } from "vitest"
import { renderHook, act } from "@testing-library/react"
import Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import { useAtlasKeyboard } from "./use-atlas-keyboard"

function makeGraph(): Graph<AtlasNodeAttributes, AtlasEdgeAttributes> {
  const g = new Graph<AtlasNodeAttributes, AtlasEdgeAttributes>()
  for (let i = 0; i < 3; i++) {
    g.addNode(`n${i}`, {
      id: `n${i}`,
      name: `Node ${i}`,
      type: "haloed",
      entityType: "Person",
      community: null,
      mention_count: 1,
      trust_state: "verified",
      recency_score: 0.5,
      focused: i === 0,
      x: i,
      y: 0,
      size: 10,
      label: `Node ${i}`,
      color: "#FFFFFF", // drift-allowed: hex value required to satisfy the AtlasNodeAttributes interface in test stubs
      haloColor: "#5AECCB", // drift-allowed: hex value required to satisfy the AtlasNodeAttributes interface in test stubs
      pulseIntensity: 0.5,
    })
  }
  return g
}

interface StubCameraState {
  x: number
  y: number
  ratio: number
}

function makeStubSigma() {
  const state: StubCameraState = { x: 0, y: 0, ratio: 1 }
  const animate = vi.fn(
    (target: Partial<StubCameraState>) => {
      Object.assign(state, target)
    },
  )
  const animatedZoom = vi.fn(({ factor }: { factor: number }) => {
    state.ratio *= factor
  })
  const animatedReset = vi.fn(() => {
    state.x = 0
    state.y = 0
    state.ratio = 1
  })
  const camera = {
    getState: () => state,
    animate,
    animatedZoom,
    animatedReset,
  }
  return {
    instance: {
      getCamera: () => camera,
      refresh: vi.fn(),
      getNodeDisplayData: (id: string) => ({ x: id === "n0" ? 0 : 1, y: 0 }),
    },
    camera,
    state,
  }
}

function keyEvent(key: string, modifiers: { meta?: boolean; shift?: boolean; ctrl?: boolean } = {}) {
  return {
    key,
    metaKey: !!modifiers.meta,
    ctrlKey: !!modifiers.ctrl,
    shiftKey: !!modifiers.shift,
    target: document.createElement("div"),
    preventDefault: vi.fn(),
  } as unknown as React.KeyboardEvent<HTMLDivElement>
}

describe("useAtlasKeyboard — selection", () => {
  it("cycles selection forward on Tab", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )

    expect(result.current.selectedNodeId).toBeNull()
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    expect(result.current.selectedNodeId).toBe("n0")
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    expect(result.current.selectedNodeId).toBe("n1")
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    expect(result.current.selectedNodeId).toBe("n2")
    // Wraps around
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    expect(result.current.selectedNodeId).toBe("n0")
  })

  it("cycles backward on Shift-Tab", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Tab", { shift: true })))
    expect(result.current.selectedNodeId).toBe("n2")
  })

  it("N key is an alias for Tab forward", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("n")))
    expect(result.current.selectedNodeId).toBe("n0")
    act(() => result.current.onKeyDown(keyEvent("N")))
    expect(result.current.selectedNodeId).toBe("n1")
  })
})

describe("useAtlasKeyboard — activation", () => {
  it("Enter calls onActivate with selected node id", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onActivate = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onActivate,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    act(() => result.current.onKeyDown(keyEvent("Enter")))
    expect(onActivate).toHaveBeenCalledWith("n0")
  })

  it("Enter is a no-op when no node selected", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onActivate = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onActivate,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Enter")))
    expect(onActivate).not.toHaveBeenCalled()
  })
})

describe("useAtlasKeyboard — camera", () => {
  it("ArrowLeft pans negative x", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("ArrowLeft")))
    expect(stub.camera.animate).toHaveBeenCalledWith(
      expect.objectContaining({ x: expect.any(Number) }),
      expect.objectContaining({ duration: expect.any(Number) }),
    )
  })

  it("+ zooms in (factor < 1)", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("+")))
    expect(stub.camera.animatedZoom).toHaveBeenCalled()
    const call = stub.camera.animatedZoom.mock.calls[0][0] as { factor: number }
    expect(call.factor).toBeLessThan(1)
  })

  it("- zooms out (factor > 1)", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("-")))
    const call = stub.camera.animatedZoom.mock.calls[0][0] as { factor: number }
    expect(call.factor).toBeGreaterThan(1)
  })

  it("R resets the camera", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("r")))
    expect(stub.camera.animatedReset).toHaveBeenCalled()
  })

  it("H recenters on focal entity", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("h")))
    expect(stub.camera.animate).toHaveBeenCalled()
  })
})

describe("useAtlasKeyboard — overlays", () => {
  it("L triggers onToggleLensMenu", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onToggleLensMenu = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onToggleLensMenu,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("l")))
    expect(onToggleLensMenu).toHaveBeenCalledTimes(1)
  })

  it("⌘K triggers onSearchPalette", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onSearchPalette = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onSearchPalette,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("k", { meta: true })))
    expect(onSearchPalette).toHaveBeenCalledTimes(1)
  })

  it("Ctrl-K also triggers onSearchPalette", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onSearchPalette = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onSearchPalette,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("k", { ctrl: true })))
    expect(onSearchPalette).toHaveBeenCalledTimes(1)
  })
})

describe("useAtlasKeyboard — input-aware", () => {
  it("ignores keys when focus is in an input", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onSearchPalette = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onSearchPalette,
      }),
    )
    const event = keyEvent("k", { meta: true }) as unknown as { target: HTMLElement }
    event.target = document.createElement("input")
    act(() => result.current.onKeyDown(event as never))
    expect(onSearchPalette).not.toHaveBeenCalled()
  })
})

describe("useAtlasKeyboard — hops + unpin", () => {
  it("1/2/3 call onHopsChange with correct hop depth", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onHopsChange = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onHopsChange,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("1")))
    expect(onHopsChange).toHaveBeenCalledWith(1)
    act(() => result.current.onKeyDown(keyEvent("2")))
    expect(onHopsChange).toHaveBeenCalledWith(2)
    act(() => result.current.onKeyDown(keyEvent("3")))
    expect(onHopsChange).toHaveBeenCalledWith(3)
  })

  it("Escape calls onUnpin", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onUnpin = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onUnpin,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Escape")))
    expect(onUnpin).toHaveBeenCalledTimes(1)
  })
})

describe("useAtlasKeyboard — onActivate fires exactly once per Enter", () => {
  it("onActivate is called exactly once when a node is selected", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onActivate = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onActivate,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    act(() => result.current.onKeyDown(keyEvent("Enter")))
    expect(onActivate).toHaveBeenCalledTimes(1)
    expect(onActivate).toHaveBeenCalledWith("n0")
  })

  it("onActivate is not called on second Enter without reselecting", () => {
    const graph = makeGraph()
    const stub = makeStubSigma()
    const onActivate = vi.fn()
    const { result } = renderHook(() =>
      useAtlasKeyboard({
        sigma: stub.instance as never,
        graph,
        focalEntity: "n0",
        onActivate,
      }),
    )
    act(() => result.current.onKeyDown(keyEvent("Tab")))
    act(() => result.current.onKeyDown(keyEvent("Enter")))
    act(() => result.current.onKeyDown(keyEvent("Enter")))
    // onActivate should be called each time Enter is pressed with a selection
    expect(onActivate).toHaveBeenCalledTimes(2)
  })
})
