import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGetSafe } from "@/lib/api";
import { fmtMoney, fmtPct, fmtNum, fmtTs } from "@/lib/format";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Badge, breakerStatus, pnlColor } from "@/components/ui/Badge";
import { EquityChart } from "@/components/EquityChart";
import { RecentActivity } from "@/components/RecentActivity";
import {
  SEGMENTS,
  SEGMENT_LABEL,
  type Segment,
  type SegmentOverview,
  type EquityPoint,
  type Position,
  type Opportunity,
  type TradesPage,
  type RiskSummary,
} from "@/lib/types";

// force-dynamic (see lib/api.ts) — positions/marks/risk change on the
// engine's own 60s tick cadence; a page that only refreshes at build
// time or on a cache-revalidate window is the wrong trade here.
export const dynamic = "force-dynamic";

export default async function SegmentPage(props: PageProps<"/segment/[segment]">) {
  const { segment: raw } = await props.params;
  if (!SEGMENTS.includes(raw as Segment)) notFound();
  const segment = raw as Segment;

  const [overview, equity, positions, opportunities, trades, risk] = await Promise.all([
    apiGetSafe<SegmentOverview>(`/segments/${segment}/overview`),
    apiGetSafe<EquityPoint[]>(`/segments/${segment}/equity-curve?window=30d`),
    apiGetSafe<Position[]>(`/segments/${segment}/positions`),
    apiGetSafe<Opportunity[]>(`/segments/${segment}/opportunities`),
    apiGetSafe<TradesPage>(`/segments/${segment}/trades?page_size=20`),
    apiGetSafe<RiskSummary>(`/segments/${segment}/risk`),
  ]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{SEGMENT_LABEL[segment]}</h1>
        {overview && (
          <Badge status={breakerStatus(overview.breaker_status)}>{overview.breaker_status}</Badge>
        )}
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
          <StatTile
            label="Equity"
            value={fmtMoney(overview?.equity.current_equity ?? null)}
            delta={overview ? fmtPct(overview.equity.total_return_pct) : undefined}
            deltaGood={overview?.equity.total_return_pct !== undefined && overview?.equity.total_return_pct !== null
              ? overview.equity.total_return_pct >= 0
              : null}
          />
          <StatTile
            label="Max drawdown"
            value={overview ? fmtPct(overview.equity.max_drawdown_pct) : "—"}
          />
          <StatTile
            label="Open positions"
            value={String(overview?.open_positions ?? "—")}
            sub={`${overview?.signals_24h ?? 0} signals / 24h`}
          />
          <StatTile
            label="Win rate (7d)"
            value={overview ? fmtPct(overview.performance_7d.win_rate) : "—"}
            sub={overview ? `${overview.performance_7d.n_closed} closed` : undefined}
          />
        </div>
      </Card>

      <Card title="Equity curve (30d)">
        <EquityChart points={equity ?? []} />
      </Card>

      <Card title="Open positions">
        {!positions || positions.length === 0 ? (
          <Empty text="No open positions." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ color: "var(--text-muted)" }} className="text-left">
                  <th className="pb-2 font-normal">Instrument</th>
                  <th className="pb-2 font-normal">Qty</th>
                  <th className="pb-2 font-normal">Entry</th>
                  <th className="pb-2 font-normal">Mark</th>
                  <th className="pb-2 font-normal">Unrealized</th>
                  <th className="pb-2 font-normal">IV</th>
                  <th className="pb-2 font-normal">Delta</th>
                  <th className="pb-2 font-normal">Stop</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.trade_id} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5">{p.instrument_symbol}</td>
                    <td className="py-1.5">{p.qty_lots}</td>
                    <td className="py-1.5">{fmtNum(p.avg_entry)}</td>
                    <td className="py-1.5">{fmtNum(p.mark)}</td>
                    <td className="py-1.5">
                      <span style={{ color: pnlColor(p.unrealized) }}>
                        {fmtMoney(p.unrealized)}
                      </span>
                    </td>
                    <td className="py-1.5">{fmtNum(p.greeks?.iv)}</td>
                    <td className="py-1.5">{fmtNum(p.greeks?.delta)}</td>
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>{p.stop_price ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Opportunities (last hour)">
        {!opportunities || opportunities.length === 0 ? (
          <Empty text="No signals in the last hour." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ color: "var(--text-muted)" }} className="text-left">
                  <th className="pb-2 font-normal">Underlying</th>
                  <th className="pb-2 font-normal">Direction</th>
                  <th className="pb-2 font-normal">Confidence</th>
                  <th className="pb-2 font-normal">Decision</th>
                  <th className="pb-2 font-normal">Reason</th>
                  <th className="pb-2 font-normal">Time</th>
                </tr>
              </thead>
              <tbody>
                {opportunities.map((o) => (
                  <tr key={o.signal_id} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5">{o.underlying_symbol}</td>
                    <td className="py-1.5 uppercase">{o.direction}</td>
                    <td className="py-1.5">{fmtNum(o.confidence, 3)}</td>
                    <td className="py-1.5">
                      <Badge status={o.decision === "TAKEN" ? "good" : "neutral"}>{o.decision}</Badge>
                    </td>
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                      {o.reject_stage ? `${o.reject_stage} / ${o.reject_reason}` : "—"}
                    </td>
                    <td className="py-1.5" style={{ color: "var(--text-muted)" }}>{fmtTs(o.ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Trade history">
          {!trades || trades.trades.length === 0 ? (
            <Empty text="No trades yet." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ color: "var(--text-muted)" }} className="text-left">
                    <th className="pb-2 font-normal">Instrument</th>
                    <th className="pb-2 font-normal">Opened</th>
                    <th className="pb-2 font-normal">Net P&L</th>
                    <th className="pb-2 font-normal">R</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.trades.map((t) => (
                    <tr key={t.trade_id} className="border-t" style={{ borderColor: "var(--border)" }}>
                      <td className="py-1.5">
                        <Link href={`/segment/${segment}/trades/${t.trade_id}`} className="hover:underline">
                          {t.instrument_symbol}
                        </Link>
                      </td>
                      <td className="py-1.5" style={{ color: "var(--text-muted)" }}>{fmtTs(t.opened_at)}</td>
                      <td className="py-1.5">
                        <span style={{ color: pnlColor(t.net_pnl) }}>
                          {fmtMoney(t.net_pnl)}
                        </span>
                      </td>
                      <td className="py-1.5">{fmtNum(t.r_multiple)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                {trades.total} total, showing page {trades.page}
              </p>
            </div>
          )}
        </Card>

        <Card title="Risk">
          {!risk ? (
            <Empty text="No risk_state yet." />
          ) : (
            <div className="space-y-2 text-sm">
              <Row label="Daily P&L" value={fmtMoney(risk.daily_pnl)} />
              <Row label="Weekly P&L" value={fmtMoney(risk.weekly_pnl)} />
              <Row label="Consecutive losses" value={String(risk.consecutive_losses)} />
              <Row label="Risk multiplier" value={fmtNum(risk.risk_multiplier)} />
              <Row
                label="Breaker"
                value={
                  <Badge status={breakerStatus(risk.breaker_status)}>
                    {risk.breaker_status}
                    {risk.breaker_reason ? ` — ${risk.breaker_reason}` : ""}
                  </Badge>
                }
              />
              <Row label="Max concurrent" value={String(risk.config.max_concurrent ?? "—")} />
              <Row label="Hard ceiling" value={fmtPct(Number(risk.config.hard_ceiling_pct))} />
            </div>
          )}
        </Card>
      </div>

      <Card title="Live activity">
        <RecentActivity segments={[segment]} />
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b py-1" style={{ borderColor: "var(--border)" }}>
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm" style={{ color: "var(--text-muted)" }}>{text}</p>;
}
