export function fmtMoney(v: number | null | undefined, ccy = ""): string {
  if (v === null || v === undefined) return "—";
  const formatted = v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return ccy ? `${ccy} ${formatted}` : formatted;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

export function fmtAge(secs: number | null | undefined): string {
  if (secs === null || secs === undefined) return "never";
  if (secs < 60) return `${Math.round(secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${(secs / 3600).toFixed(1)}h ago`;
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
