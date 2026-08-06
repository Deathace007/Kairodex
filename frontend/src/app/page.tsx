import { apiGetSafe } from "@/lib/api";
import { fmtMoney, fmtPct, fmtAge, fmtTs } from "@/lib/format";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Badge } from "@/components/ui/Badge";
import { RecentActivity } from "@/components/RecentActivity";
import { SEGMENT_LABEL } from "@/lib/types";
import type { MasterOverview, FeedHealthRow, StrategyRow } from "@/lib/types";
import Link from "next/link";

// force-dynamic (see lib/api.ts) — same reasoning as the segment page.
export const dynamic = "force-dynamic";

export default async function MasterPage() {
  const [overview, feeds, strategies] = await Promise.all([
    apiGetSafe<MasterOverview>("/master/overview"),
    apiGetSafe<FeedHealthRow[]>("/health/feeds"),
    apiGetSafe<StrategyRow[]>("/strategies"),
  ]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-xl font-semibold">Master overview</h1>

      <Card title="Segments">
        {!overview || overview.segments.length === 0 ? (
          <Empty text="No equity_snapshots yet for any segment." />
        ) : (
          <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
            {overview.segments.map((s) => (
              <Link key={s.segment} href={`/segment/${s.segment}`} className="block">
                <StatTile
                  label={SEGMENT_LABEL[s.segment]}
                  value={fmtMoney(s.equity, s.currency)}
                  delta={s.max_drawdown_pct !== null ? `DD ${fmtPct(s.max_drawdown_pct)}` : undefined}
                  deltaGood={s.max_drawdown_pct !== null ? Number(s.max_drawdown_pct) < 0.05 : null}
                  sub={s.breaker_status}
                />
              </Link>
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Feed health">
          {!feeds || feeds.length === 0 ? (
            <Empty text="No feed_health rows yet — has ingest started?" />
          ) : (
            <table className="w-full text-sm">
              <tbody>
                {feeds.map((f) => (
                  <tr key={f.provider} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5">{f.provider}</td>
                    <td className="py-1.5">
                      <Badge status={f.connected ? "good" : "critical"}>
                        {f.connected ? "connected" : "down"}
                      </Badge>
                    </td>
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                      {fmtAge(f.last_message_age_secs)}
                    </td>
                    <td className="py-1.5 text-right" style={{ color: "var(--text-secondary)" }}>
                      gap {fmtPct(f.gap_rate_24h)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Strategies">
          {!strategies || strategies.length === 0 ? (
            <Empty text="No strategies rows yet." />
          ) : (
            <table className="w-full text-sm">
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.strategy_id} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1.5">{s.name} v{s.version}</td>
                    <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                      {SEGMENT_LABEL[s.segment]}
                    </td>
                    <td className="py-1.5 text-right">
                      <Badge status="neutral">{s.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Research insights">
        {!overview || overview.research_insights.length === 0 ? (
          <Empty text="No research_notes yet — kairodex research import-notes writes here." />
        ) : (
          <ul className="space-y-3 text-sm">
            {overview.research_insights.map((n) => (
              <li key={n.note_id} className="border-b pb-2 last:border-0" style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center justify-between">
                  <span style={{ color: "var(--text-secondary)" }}>
                    {n.segment ? SEGMENT_LABEL[n.segment] : "all segments"} — {fmtTs(n.created_at)}
                  </span>
                  <Badge status="neutral">{n.status}</Badge>
                </div>
                <pre className="mt-1 overflow-x-auto text-xs" style={{ color: "var(--text-primary)" }}>
                  {JSON.stringify(n.findings, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Live activity">
        <RecentActivity segments={null} />
      </Card>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm" style={{ color: "var(--text-muted)" }}>{text}</p>;
}
