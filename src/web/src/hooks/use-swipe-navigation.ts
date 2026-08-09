import { useCallback, useEffect, useRef } from 'react';

interface SwipeOptions {
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
  threshold?: number;
  maxDuration?: number;
  edgeWidth?: number;
}

export function useSwipeNavigation({
  onSwipeLeft,
  onSwipeRight,
  threshold = 50,
  maxDuration = 300,
  edgeWidth = 30,
}: SwipeOptions = {}) {
  const touchStart = useRef<{ x: number; y: number; time: number } | null>(null);

  const handleTouchStart = useCallback((e: TouchEvent) => {
    const touch = e.touches[0];
    // Only track swipes starting near edges for navigation
    if (touch.clientX < edgeWidth || touch.clientX > window.innerWidth - edgeWidth) {
      touchStart.current = { x: touch.clientX, y: touch.clientY, time: Date.now() };
    }
  }, [edgeWidth]);

  const handleTouchEnd = useCallback((e: TouchEvent) => {
    if (!touchStart.current) return;
    const touch = e.changedTouches[0];
    const dx = touch.clientX - touchStart.current.x;
    const dy = touch.clientY - touchStart.current.y;
    const dt = Date.now() - touchStart.current.time;
    touchStart.current = null;

    // Must be primarily horizontal and within time limit
    if (Math.abs(dx) < threshold || Math.abs(dy) > Math.abs(dx) || dt > maxDuration) return;

    if (dx > 0 && onSwipeRight) onSwipeRight();
    if (dx < 0 && onSwipeLeft) onSwipeLeft();
  }, [threshold, maxDuration, onSwipeLeft, onSwipeRight]);

  useEffect(() => {
    document.addEventListener('touchstart', handleTouchStart, { passive: true });
    document.addEventListener('touchend', handleTouchEnd, { passive: true });
    return () => {
      document.removeEventListener('touchstart', handleTouchStart);
      document.removeEventListener('touchend', handleTouchEnd);
    };
  }, [handleTouchStart, handleTouchEnd]);
}
