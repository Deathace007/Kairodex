// kairodex.api serializes Python `Decimal` fields (money: avg_entry,
// mark, net_pnl, equity, drawdown, ...) as JSON *strings*, not numbers —
// FastAPI's own precision-preserving choice for Decimal, matching this
// codebase's "money is numeric(18,4), never float" convention all the
// way out to the wire (same reasoning as kairodex.export's bundle
// files). `float`/`int`-sourced fields (win_rate, confidence, counts)
// arrive as real JSON numbers. Every formatter here accepts both and
// coerces — caught live on the VM: real position data existed but every
// numeric cell crashed with "a.toFixed is not a function" because these
// used to assume `number` only.
type Num = number | string | null | undefined;

function toNumber(v: Num): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? n : null;
}

export function fmtMoney(v: Num, ccy = ""): string {
  const n = toNumber(v);
  if (n === null) return "—";
  const formatted = n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return ccy ? `${ccy} ${formatted}` : formatted;
}

export function fmtPct(v: Num, digits = 1): string {
  const n = toNumber(v);
  if (n === null) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function fmtNum(v: Num, digits = 2): string {
  const n = toNumber(v);
  if (n === null) return "—";
  return n.toFixed(digits);
}

export function fmtAge(secs: Num): string {
  const n = toNumber(secs);
  if (n === null) return "never";
  if (n < 60) return `${Math.round(n)}s ago`;
  if (n < 3600) return `${Math.round(n / 60)}m ago`;
  return `${(n / 3600).toFixed(1)}h ago`;
}

export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
