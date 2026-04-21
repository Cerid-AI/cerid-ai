import { useCallback, useEffect, useRef, useState } from 'react';
import { logSwallowedError } from "@/lib/log-swallowed"

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
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mountedRef = useRef(true);
  // Store callbacks in refs to avoid stale closures on reconnect
  const onDeltaRef = useRef(onDelta);
  const onPresenceRef = useRef(onPresence);
  const connectRef = useRef<() => void>(undefined);
  useEffect(() => { onDeltaRef.current = onDelta; }, [onDelta]);
  useEffect(() => { onPresenceRef.current = onPresence; }, [onPresence]);

  const connect = useCallback(() => {
    if (!enabled) return;
    try {
      const ws = new WebSocket(`${url}?token=${encodeURIComponent(token)}`);
      wsRef.current = ws;
      ws.onopen = () => { if (mountedRef.current) { setConnected(true); setError(null); } };
      ws.onclose = () => {
        if (mountedRef.current) {
          setConnected(false);
          reconnectTimer.current = setTimeout(() => connectRef.current?.(), 3000);
        }
      };
      ws.onerror = () => { if (mountedRef.current) setError('WebSocket connection failed'); };
      ws.onmessage = (event) => {
        try {
          const msg: SyncMessage = JSON.parse(event.data);
          if (msg.type === 'delta' && onDeltaRef.current) onDeltaRef.current(msg.payload);
          if (msg.type === 'presence' && onPresenceRef.current) onPresenceRef.current(msg.payload);
        } catch (err) { logSwallowedError(err, "json.parse.websocket-message") }
      };
    } catch { setError('Failed to create WebSocket'); }
  }, [url, token, enabled]);
  useEffect(() => { connectRef.current = connect; }, [connect]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
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
