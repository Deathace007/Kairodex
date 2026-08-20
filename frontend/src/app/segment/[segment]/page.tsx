import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGetSafe } from "@/lib/api";
import { fmtMoney, fmtPct, fmtNum, fmtTs, fmtTsIST, todayIST, isValidIsoDate } from "@/lib/format";
import { AutoRefresh } from "@/components/AutoRefresh";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Badge, breakerStatus, pnlColor } from "@/components/ui/Badge";
import { EquityChart } from "@/components/EquityChart";
import { RecentActivity } from "@/components/RecentActivity";
import { TradeDateFilter } from "@/components/TradeDateFilter";
import { IS_STATIC_EXPORT } from "@/lib/renderMode";
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

// The static export needs every `[segment]` value enumerated up front
// (no server to resolve an arbitrary one at request time); the live
// server build doesn't use this — its pages render dynamically per
// request anyway (lib/api.ts's `cache: "no-store"` fetches).
export const generateStaticParams = IS_STATIC_EXPORT
  ? () => SEGMENTS.map((segment) => ({ segment }))
  : undefined;

export default async function SegmentPage(props: PageProps<"/segment/[segment]">) {
  const { segment: raw } = await props.params;
  if (!SEGMENTS.includes(raw as Segment)) notFound();
  const segment = raw as Segment;

  // Static export (`output: "export"`, deploy-surge.sh) has no server,
  // so it cannot support per-request `searchParams` at all -- Next fails
  // the whole build the moment a page awaits it under that mode ("used
  // await searchParams ... couldn't be rendered statically"), same
  // reasoning `generateStaticParams` above is already gated on
  // `IS_STATIC_EXPORT`. The `if` (not a ternary awaiting inside one
  // branch) is what lets the static-export build's dead-code elimination
  // drop the `await props.searchParams` call entirely rather than merely
  // skipping it at runtime -- the Surge snapshot's date filter is
  // therefore always "today (as of last rebuild)", same accepted
  // degradation AutoRefresh's own docstring already describes for that
  // build.
  let rawDate: string | string[] | undefined;
  if (!IS_STATIC_EXPORT) {
    rawDate = (await props.searchParams).date;
  }
  const tradeDate = isValidIsoDate(Array.isArray(rawDate) ? rawDate[0] : rawDate)
    ? ((Array.isArray(rawDate) ? rawDate[0] : rawDate) as string)
    : todayIST();

  const [overview, equity, positions, opportunities, trades, risk] = await Promise.all([
    apiGetSafe<SegmentOverview>(`/segments/${segment}/overview`),
    apiGetSafe<EquityPoint[]>(`/segments/${segment}/equity-curve?window=30d`),
    apiGetSafe<Position[]>(`/segments/${segment}/positions`),
    apiGetSafe<Opportunity[]>(`/segments/${segment}/opportunities`),
    // `from`/`to` are the same calendar date -- kairodex.api's
    // segment_trades adds a day to `to` itself, so this is an inclusive
    // single-day window. Plain YYYY-MM-DD dates parse as UTC midnight
    // (api/deps.py's parse_iso_date), which still correctly brackets an
    // IST trading day: NSE hours (09:15-15:30 IST) fall entirely within
    // the same UTC calendar date, never crossing UTC midnight.
    apiGetSafe<TradesPage>(
      `/segments/${segment}/trades?from=${tradeDate}&to=${tradeDate}&page_size=200`,
    ),
    apiGetSafe<RiskSummary>(`/segments/${segment}/risk`),
  ]);
  // Latest trade on top -- the API returns ascending by opened_at
  // (analytics/loader.py's load_trades).
  const tradesForDay = trades ? [...trades.trades].reverse() : [];
  const tradesTotal = trades?.total ?? 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <AutoRefresh />
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
            deltaGood={
              overview?.equity.total_return_pct != null
                ? Number(overview.equity.total_return_pct) >= 0
                : null
            }
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
                  <th className="pb-2 font-normal">Entered (IST)</th>
                  <th className="pb-2 font-normal">Qty</th>
                  <th className="pb-2 font-normal">Entry</th>
                  <th className="pb-2 font-normal">Mark</th>
                  <th className="pb-2 font-normal">Unrealized</th>
                  <th className="pb-2 font-normal">IV</th>
                  <th className="pb-2 font-normal">Delta</th>
                  <th className="pb-2 font-normal">Stop</th>
                  <th className="pb-2 font-normal">Target</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.trade_id} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5">{p.instrument_symbol}</td>
                    <td className="py-1.5 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {fmtTsIST(p.opened_at)}
                    </td>
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
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                      {fmtNum(p.stop_price)}
                    </td>
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                      {fmtNum(p.profit_target)}
                    </td>
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
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h3
              className="text-sm font-medium tracking-wide uppercase"
              style={{ color: "var(--text-secondary)" }}
            >
              Trade history
            </h3>
            <TradeDateFilter segment={segment} date={tradeDate} />
          </div>
          {tradesForDay.length === 0 ? (
            <Empty text={`No trades on ${tradeDate}.`} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ color: "var(--text-muted)" }} className="text-left">
                    <th className="pb-2 font-normal">Instrument</th>
                    <th className="pb-2 font-normal">Entered (IST)</th>
                    <th className="pb-2 font-normal">Stop</th>
                    <th className="pb-2 font-normal">Target</th>
                    <th className="pb-2 font-normal">Net P&L</th>
                    <th className="pb-2 font-normal">R</th>
                  </tr>
                </thead>
                <tbody>
                  {tradesForDay.map((t) => (
                    <tr key={t.trade_id} className="border-t" style={{ borderColor: "var(--border)" }}>
                      <td className="py-1.5">
                        <Link href={`/segment/${segment}/trades/${t.trade_id}`} className="hover:underline">
                          {t.instrument_symbol}
                        </Link>
                      </td>
                      <td className="py-1.5 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                        {fmtTsIST(t.opened_at)}
                      </td>
                      <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                        {fmtNum(t.initial_stop_price)}
                      </td>
                      <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                        {fmtNum(t.profit_target)}
                      </td>
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
                {tradesTotal} trade{tradesTotal === 1 ? "" : "s"} on {tradeDate}
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
