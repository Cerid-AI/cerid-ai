import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

describe('useSwipeNavigation', () => {
  it('should register touch event listeners', async () => {
    const addSpy = vi.spyOn(document, 'addEventListener');
    const { useSwipeNavigation } = await import('../hooks/use-swipe-navigation');
    renderHook(() => useSwipeNavigation({ onSwipeLeft: vi.fn(), onSwipeRight: vi.fn() }));
    expect(addSpy).toHaveBeenCalledWith('touchstart', expect.any(Function), { passive: true });
    expect(addSpy).toHaveBeenCalledWith('touchend', expect.any(Function), { passive: true });
    addSpy.mockRestore();
  });

  it('should clean up listeners on unmount', async () => {
    const removeSpy = vi.spyOn(document, 'removeEventListener');
    const { useSwipeNavigation } = await import('../hooks/use-swipe-navigation');
    const { unmount } = renderHook(() => useSwipeNavigation());
    unmount();
    expect(removeSpy).toHaveBeenCalledWith('touchstart', expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith('touchend', expect.any(Function));
    removeSpy.mockRestore();
  });
});
