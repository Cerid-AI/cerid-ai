import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// Mock IndexedDB
const mockStore = new Map<string, unknown>();
const mockIDB = {
  open: vi.fn(() => ({
    onupgradeneeded: null as (() => void) | null,
    onsuccess: null as (() => void) | null,
    onerror: null as (() => void) | null,
    result: {
      createObjectStore: vi.fn(),
      transaction: vi.fn(() => ({
        objectStore: vi.fn(() => ({
          put: vi.fn((item: { id: string }) => { mockStore.set(item.id, item); }),
          getAll: vi.fn(() => ({ result: Array.from(mockStore.values()), onsuccess: null as (() => void) | null, onerror: null })),
          delete: vi.fn((id: string) => { mockStore.delete(id); }),
          clear: vi.fn(() => { mockStore.clear(); }),
        })),
        oncomplete: null as (() => void) | null,
        onerror: null as (() => void) | null,
      })),
    },
  })),
};

vi.stubGlobal('indexedDB', mockIDB);

describe('useOfflineQueue', () => {
  beforeEach(() => {
    mockStore.clear();
    vi.stubGlobal('navigator', { onLine: true });
  });

  it('should detect online status', async () => {
    const { useOfflineQueue } = await import('../hooks/use-offline-queue');
    const { result } = renderHook(() => useOfflineQueue());
    expect(result.current.isOnline).toBe(true);
  });

  it('should start with zero queue size', async () => {
    const { useOfflineQueue } = await import('../hooks/use-offline-queue');
    const { result } = renderHook(() => useOfflineQueue());
    expect(result.current.queueSize).toBe(0);
  });

  it('should not be replaying initially', async () => {
    const { useOfflineQueue } = await import('../hooks/use-offline-queue');
    const { result } = renderHook(() => useOfflineQueue());
    expect(result.current.isReplaying).toBe(false);
  });
});
