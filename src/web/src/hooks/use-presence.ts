import { useCallback, useEffect, useState } from 'react';

interface PresenceUser {
  user_id: string;
  display_name?: string;
  active_domain?: string;
  cursor_artifact_id?: string;
  last_seen?: string;
}

interface UsePresenceOptions {
  syncHook: {
    connected: boolean;
    sendPresence: (payload: Record<string, unknown>) => void;
  };
  userId: string;
  displayName?: string;
  heartbeatMs?: number;
}

export function usePresence({ syncHook, userId, displayName, heartbeatMs = 30000 }: UsePresenceOptions) {
  const [users, setUsers] = useState<PresenceUser[]>([]);

  // Handle incoming presence updates
  const handlePresence = useCallback((payload: Record<string, unknown>) => {
    if (payload.type === 'update' && Array.isArray(payload.users)) {
      setUsers(payload.users as PresenceUser[]);
    }
  }, []);

  // Send heartbeat
  useEffect(() => {
    if (!syncHook.connected) return;
    const send = () => syncHook.sendPresence({ user_id: userId, display_name: displayName });
    send();
    const interval = setInterval(send, heartbeatMs);
    return () => clearInterval(interval);
  }, [syncHook, userId, displayName, heartbeatMs]);

  return { users, handlePresence };
}
