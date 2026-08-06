"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";

export interface StreamMessage {
  type: "tick" | "signal" | "position_update" | "trade_closed" | "risk_update" | "feed_health";
  segment: string | null;
  ts: string;
  data: Record<string, unknown>;
}

/**
 * ARCHITECTURE.md §15/§16 — "one socket for the whole UI." Reconnects
 * with a fixed 3s backoff on drop (a paper-trading dashboard has no
 * urgency that needs exponential backoff tuning) and keeps only the last
 * `limit` messages — this is a live feed for a "recent activity" panel,
 * not a store of record (trades/trade_events, read via REST, are that).
 */
export function useStream(segments: string[] | null, limit = 50): StreamMessage[] {
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      if (cancelled) return;
      const wsBase = API_BASE.replace(/^http/, "ws");
      const qs = segments && segments.length > 0 ? `?segments=${segments.join(",")}` : "";
      const ws = new WebSocket(`${wsBase}/ws/stream${qs}`);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as StreamMessage;
          setMessages((prev) => [msg, ...prev].slice(0, limit));
        } catch {
          // malformed frame — drop it, never crash the panel over one bad message
        }
      };
      ws.onclose = () => {
        if (!cancelled) reconnectTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- segments compared by join() below
  }, [segments?.join(","), limit]);

  return messages;
}
