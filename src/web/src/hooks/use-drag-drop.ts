// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useCallback } from "react"

interface DragHandlers {
  onDragEnter: (e: React.DragEvent) => void
  onDragLeave: (e: React.DragEvent) => void
  onDragOver: (e: React.DragEvent) => void
  onDrop: (e: React.DragEvent) => void
}

interface UseDragDropReturn {
  isDragOver: boolean
  dragHandlers: DragHandlers
}

/**
 * Shared drag-and-drop hook for file ingestion.
 *
 * Manages the dragCounter pattern (needed because dragEnter/dragLeave
 * fire for every child element) and exposes a clean `isDragOver` boolean.
 *
 * @param onFiles Callback invoked with the dropped File[] array.
 */
export function useDragDrop(onFiles: (files: File[]) => void): UseDragDropReturn {
  const [isDragOver, setIsDragOver] = useState(false)
  const dragCounterRef = useRef(0)

  const onDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (e.dataTransfer.types.includes("Files")) setIsDragOver(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) setIsDragOver(false)
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    dragCounterRef.current = 0
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) onFiles(files)
  }, [onFiles])

  return {
    isDragOver,
    dragHandlers: { onDragEnter, onDragLeave, onDragOver, onDrop },
  }
}
