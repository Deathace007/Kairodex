"use client";

import { useStream } from "@/lib/useStream";
import { fmtTs } from "@/lib/format";
import { Badge } from "./ui/Badge";

const TYPE_LABEL: Record<string, string> = {
  signal: "Signal",
  trade_closed: "Trade closed",
  position_update: "Position",
  risk_update: "Risk",
  feed_health: "Feed",
  tick: "Tick",
};

/** The one WS-driven panel on either dashboard — ARCHITECTURE.md §15/§16's
 * "one socket for the whole UI," made visible. Everything else on these
 * pages is server-fetched REST (simpler, and correct for numbers that
 * only change once a minute); this panel is what actually demonstrates
 * the stream is live. */
export function RecentActivity({ segments }: { segments: string[] | null }) {
  const messages = useStream(segments, 30);

  if (messages.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No live activity yet — the WS stream connects on page load; the engine
        publishes on its own 60s tick cadence.
      </p>
    );
  }

  return (
    <ul className="max-h-80 space-y-2 overflow-y-auto text-sm">
      {messages.map((m, i) => (
        <li key={i} className="flex items-start justify-between gap-3 border-b pb-2" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-2">
            <Badge status="neutral">{TYPE_LABEL[m.type] ?? m.type}</Badge>
            {m.segment && (
              <span style={{ color: "var(--text-secondary)" }}>{m.segment}</span>
            )}
          </div>
          <div className="text-right">
            <div style={{ color: "var(--text-primary)" }}>{summarize(m)}</div>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>{fmtTs(m.ts)}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function summarize(m: { type: string; data: Record<string, unknown> }): string {
  switch (m.type) {
    case "signal":
      return `${m.data.underlying_symbol ?? "?"} — ${m.data.taken ? "TAKEN" : "rejected"}`;
    case "trade_closed":
      return `trade ${m.data.trade_id} — ${m.data.reason}`;
    case "position_update":
      return `trade ${m.data.trade_id} mark ${m.data.mark ?? "—"}`;
    case "risk_update":
      return `breaker ${m.data.breaker_status}`;
    default:
      return JSON.stringify(m.data).slice(0, 60);
  }
}
