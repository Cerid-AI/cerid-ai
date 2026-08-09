import { useCallback, useEffect, useRef, useState } from 'react';

interface QueuedRequest {
  id: string;
  url: string;
  method: string;
  body?: string;
  headers?: Record<string, string>;
  timestamp: number;
}

const DB_NAME = 'cerid-offline-queue';
const STORE_NAME = 'requests';

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE_NAME, { keyPath: 'id' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function addToQueue(request: QueuedRequest): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  tx.objectStore(STORE_NAME).put(request);
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function getQueue(): Promise<QueuedRequest[]> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readonly');
  const store = tx.objectStore(STORE_NAME);
  return new Promise((resolve, reject) => {
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function clearQueue(): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  tx.objectStore(STORE_NAME).clear();
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function removeFromQueue(id: string): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  tx.objectStore(STORE_NAME).delete(id);
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export function useOfflineQueue() {
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [queueSize, setQueueSize] = useState(0);
  const [isReplaying, setIsReplaying] = useState(false);
  const replayingRef = useRef(false);

  useEffect(() => {
    const onOnline = () => setIsOnline(true);
    const onOffline = () => setIsOnline(false);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  }, []);

  const enqueue = useCallback(async (url: string, method: string, body?: string, headers?: Record<string, string>) => {
    const request: QueuedRequest = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      url, method, body, headers,
      timestamp: Date.now(),
    };
    await addToQueue(request);
    setQueueSize(prev => prev + 1);
  }, []);

  const replay = useCallback(async () => {
    if (replayingRef.current) return;
    replayingRef.current = true;
    setIsReplaying(true);
    try {
      const queue = await getQueue();
      for (const req of queue.sort((a, b) => a.timestamp - b.timestamp)) {
        try {
          await fetch(req.url, {
            method: req.method,
            body: req.body,
            headers: req.headers,
          });
          await removeFromQueue(req.id);
          setQueueSize(prev => Math.max(0, prev - 1));
        } catch {
          break; // Stop replay on first failure (likely still offline)
        }
      }
    } finally {
      replayingRef.current = false;
      setIsReplaying(false);
    }
  }, []);

  // Auto-replay when coming back online
  useEffect(() => {
    if (isOnline && queueSize > 0) {
      replay();
    }
  }, [isOnline, queueSize, replay]);

  // Load initial queue size
  useEffect(() => {
    getQueue().then(q => setQueueSize(q.length)).catch(() => {});
  }, []);

  return { isOnline, queueSize, isReplaying, enqueue, replay };
}
