type Status = "good" | "warning" | "serious" | "critical" | "neutral";

const DOT: Record<Status, string> = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  serious: "var(--status-serious)",
  critical: "var(--status-critical)",
  neutral: "var(--text-muted)",
};

/** Status is never color-alone (dataviz skill's status-palette rule) —
 * every badge carries its own text label next to the dot. */
export function Badge({ status, children }: { status: Status; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium"
      style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: DOT[status] }}
        aria-hidden
      />
      {children}
    </span>
  );
}

export function breakerStatus(status: string): Status {
  return status === "TRIPPED" ? "critical" : "good";
}

/** P&L text color as a direct CSS value (not a Status label — this is
 * for coloring a plain number inline, where a dot+label badge would be
 * noise; the number's own sign is the label). */
export function pnlColor(value: number | null): string {
  if (value === null) return "var(--text-secondary)";
  if (value > 0) return "var(--status-good)";
  if (value < 0) return "var(--status-critical)";
  return "var(--text-secondary)";
}
