import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGetSafe } from "@/lib/api";
import { fmtMoney, fmtNum, fmtTs } from "@/lib/format";
import { Card } from "@/components/ui/Card";
import { SEGMENTS, SEGMENT_LABEL, type Segment } from "@/lib/types";

interface TradeEvent {
  seq: number;
  ts: string;
  event_type: string;
  payload: Record<string, unknown>;
}

interface TradeDetail {
  trade: {
    trade_id: number;
    strategy_id: number;
    instrument_symbol: string | null;
    underlying_symbol: string | null;
    opened_at: string;
    closed_at: string | null;
    qty_lots: number;
    lot_size: number;
    avg_entry: number;
    avg_exit: number | null;
    gross_pnl: number | null;
    net_pnl: number | null;
    fees: number | null;
    holding_secs: number | null;
    exit_reason: string | null;
    risk_params: Record<string, unknown> | null;
    context_entry: Record<string, unknown> | null;
    context_exit: Record<string, unknown> | null;
  };
  instrument_symbol: string | null;
  underlying_symbol: string | null;
  events: TradeEvent[];
  chart_ref: unknown;
}

// ARCHITECTURE.md §15's drill-down endpoint — "full event timeline +
// chart_ref." force-dynamic (see lib/api.ts) — a trade's event timeline
// changes with every fill/partial exit, never pre-render this stale.
export const dynamic = "force-dynamic";

export default async function TradeDetailPage(
  props: PageProps<"/segment/[segment]/trades/[tradeId]">,
) {
  const { segment: raw, tradeId } = await props.params;
  if (!SEGMENTS.includes(raw as Segment)) notFound();
  const segment = raw as Segment;

  const detail = await apiGetSafe<TradeDetail>(`/segments/${segment}/trades/${tradeId}`);
  if (!detail) notFound();

  const t = detail.trade;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link href={`/segment/${segment}`} className="text-sm hover:underline" style={{ color: "var(--text-secondary)" }}>
          ← {SEGMENT_LABEL[segment]}
        </Link>
        <h1 className="mt-1 text-xl font-semibold">
          Trade {t.trade_id} — {t.instrument_symbol ?? "?"}
        </h1>
      </div>

      <Card title="Summary">
        <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
          <Field label="Underlying" value={t.underlying_symbol ?? "—"} />
          <Field label="Opened" value={fmtTs(t.opened_at)} />
          <Field label="Closed" value={t.closed_at ? fmtTs(t.closed_at) : "open"} />
          <Field label="Qty (lots)" value={String(t.qty_lots)} />
          <Field label="Avg entry" value={fmtNum(t.avg_entry)} />
          <Field label="Avg exit" value={fmtNum(t.avg_exit)} />
          <Field label="Gross P&L" value={fmtMoney(t.gross_pnl)} />
          <Field label="Net P&L" value={fmtMoney(t.net_pnl)} />
          <Field label="Fees" value={fmtMoney(t.fees)} />
          <Field label="Exit reason" value={t.exit_reason ?? "—"} />
          <Field
            label="Holding"
            value={t.holding_secs ? `${Math.round(t.holding_secs / 60)}m` : "—"}
          />
        </dl>
      </Card>

      {(t.context_entry || t.context_exit) && (
        <Card title="Context (regime, liquidity, underlying px)">
          <div className="grid grid-cols-2 gap-6 text-sm">
            <div>
              <div className="mb-1 font-medium" style={{ color: "var(--text-secondary)" }}>Entry</div>
              <pre className="overflow-x-auto text-xs">{JSON.stringify(t.context_entry, null, 2)}</pre>
            </div>
            <div>
              <div className="mb-1 font-medium" style={{ color: "var(--text-secondary)" }}>Exit</div>
              <pre className="overflow-x-auto text-xs">{JSON.stringify(t.context_exit, null, 2)}</pre>
            </div>
          </div>
        </Card>
      )}

      <Card title="Event timeline">
        {detail.events.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No events.</p>
        ) : (
          <ol className="space-y-3 text-sm">
            {detail.events.map((e) => (
              <li key={e.seq} className="border-l-2 pl-3" style={{ borderColor: "var(--series-1)" }}>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{e.event_type}</span>
                  <span style={{ color: "var(--text-muted)" }}>{fmtTs(e.ts)}</span>
                </div>
                <pre className="mt-1 overflow-x-auto text-xs" style={{ color: "var(--text-secondary)" }}>
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs" style={{ color: "var(--text-muted)" }}>{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}
