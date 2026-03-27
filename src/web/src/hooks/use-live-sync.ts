import { useCallback, useEffect, useRef, useState } from 'react';

interface SyncMessage {
  type: 'delta' | 'presence' | 'ack' | 'error';
  payload: Record<string, unknown>;
}

interface UseLiveSyncOptions {
  url: string;
  token: string;
  enabled?: boolean;
  onDelta?: (payload: Record<string, unknown>) => void;
  onPresence?: (payload: Record<string, unknown>) => void;
}

export function useLiveSync({ url, token, enabled = true, onDelta, onPresence }: UseLiveSyncOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    if (!enabled) return;
    try {
      const ws = new WebSocket(`${url}?token=${encodeURIComponent(token)}`);
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); setError(null); };
      ws.onclose = () => {
        setConnected(false);
        reconnectTimer.current = setTimeout(connect, 3000);
      };
      ws.onerror = () => setError('WebSocket connection failed');
      ws.onmessage = (event) => {
        try {
          const msg: SyncMessage = JSON.parse(event.data);
          if (msg.type === 'delta' && onDelta) onDelta(msg.payload);
          if (msg.type === 'presence' && onPresence) onPresence(msg.payload);
        } catch { /* ignore malformed messages */ }
      };
    } catch { setError('Failed to create WebSocket'); }
  }, [url, token, enabled, onDelta, onPresence]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendDelta = useCallback((payload: Record<string, unknown>) => {
    wsRef.current?.send(JSON.stringify({ type: 'delta', payload }));
  }, []);

  const sendPresence = useCallback((payload: Record<string, unknown>) => {
    wsRef.current?.send(JSON.stringify({ type: 'presence', payload }));
  }, []);

  return { connected, error, sendDelta, sendPresence };
}
