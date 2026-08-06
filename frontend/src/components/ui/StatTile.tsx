/** Tier-1 "stat tile" (dataviz skill) — value + optional delta, no chart
 * when a chart isn't the job (§1 of the skill's procedure: "the job
 * picks the chart type, and sometimes the answer is not a chart"). Most
 * of this dashboard is exactly that case. */
export function StatTile({
  label,
  value,
  delta,
  deltaGood,
  sub,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaGood?: boolean | null;
  sub?: string;
}) {
  const deltaColor =
    deltaGood === true
      ? "var(--status-good)"
      : deltaGood === false
        ? "var(--status-critical)"
        : "var(--text-secondary)";
  return (
    <div>
      <div className="text-xs" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums" style={{ color: "var(--text-primary)" }}>
        {value}
      </div>
      {(delta || sub) && (
        <div className="mt-1 flex items-center gap-2 text-xs">
          {delta && (
            <span className="tabular-nums" style={{ color: deltaColor }}>
              {delta}
            </span>
          )}
          {sub && <span style={{ color: "var(--text-muted)" }}>{sub}</span>}
        </div>
      )}
    </div>
  );
}
