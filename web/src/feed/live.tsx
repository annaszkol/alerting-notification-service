// One WebSocket per browser tab (contracts/ws-messages.md). Pages register listeners and set the feed filters.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getToken, LOGOUT_EVENT, setToken } from "../api/client";
import type { AlertSummary } from "../api/types";

export type LiveStatus = "connecting" | "live" | "offline";
export type ServerMessage =
  | { type: "alert.upsert"; alert: AlertSummary }
  | { type: "subscribed"; filters: Record<string, unknown> }
  | { type: "resync"; reason: string }
  | { type: "ping" };
export interface ServerFilters {
  team?: string;
  severity?: string[];
  state?: string[];
}

interface Live {
  status: LiveStatus;
  setFilters(filters: ServerFilters): void;
  listen(listener: (message: ServerMessage) => void): () => void;
}

const LiveContext = createContext<Live | null>(null);
const MAX_BACKOFF_MS = 30_000;

export function backoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS); // 1 s, 2 s, 4 s ... 30 s (rule 3)
}

export function LiveProvider({ token, children }: { token: string | null; children: ReactNode }) {
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const socket = useRef<WebSocket | null>(null);
  const filters = useRef<ServerFilters>({});
  const listeners = useRef(new Set<(message: ServerMessage) => void>());

  const send = useCallback((message: unknown) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  }, []);

  useEffect(() => {
    if (!token) return;
    let stopped = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setStatus("connecting");
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${scheme}://${window.location.host}/ws?token=${encodeURIComponent(getToken() ?? "")}`);
      const openedAt = Date.now();
      socket.current = ws;
      ws.onopen = () => {
        setStatus("live");
        ws.send(JSON.stringify({ type: "subscribe", filters: filters.current })); // rule 1
      };
      ws.onmessage = (event) => {
        let message: ServerMessage;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type === "ping") {
          ws.send(JSON.stringify({ type: "pong" }));
          return;
        }
        listeners.current.forEach((listener) => listener(message));
      };
      ws.onclose = (event) => {
        if (socket.current === ws) socket.current = null;
        if (stopped) return;
        if (event.code === 4401) {
          setToken(null);
          window.dispatchEvent(new Event(LOGOUT_EVENT));
          return;
        }
        setStatus("offline");
        if (Date.now() - openedAt >= MAX_BACKOFF_MS) attempt = 0; // a connection that lasted resets the backoff
        timer = setTimeout(connect, backoffMs(attempt++));
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket.current?.close();
      socket.current = null;
    };
  }, [token]);

  const value = useMemo<Live>(
    () => ({
      status,
      setFilters(next) {
        filters.current = next;
        send({ type: "subscribe", filters: next });
      },
      listen(listener) {
        listeners.current.add(listener);
        return () => listeners.current.delete(listener);
      },
    }),
    [status, send],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): Live {
  const live = useContext(LiveContext);
  if (!live) throw new Error("useLive outside LiveProvider");
  return live;
}
