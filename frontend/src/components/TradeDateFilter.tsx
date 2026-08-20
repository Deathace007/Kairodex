"use client";

import { useRouter } from "next/navigation";
import type { Segment } from "@/lib/types";

/** A single native date input, driving the `?date=` search param on the
 * segment page itself (URL-driven, server-refetched — same pattern
 * AutoRefresh already relies on for this app: no client-side data
 * fetching duplicated here, `router.refresh()`'s cousin `router.push`
 * just re-triggers the server component with a new query string). The
 * date is always interpreted as an IST calendar date (see
 * lib/format.ts's `todayIST`) — the same reference timezone every
 * "Entered (IST)" column already uses. */
export function TradeDateFilter({ segment, date }: { segment: Segment; date: string }) {
  const router = useRouter();

  return (
    <label className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
      Date
      <input
        type="date"
        defaultValue={date}
        onChange={(e) => {
          if (!e.target.value) return;
          router.push(`/segment/${segment}?date=${e.target.value}`);
        }}
        className="rounded border bg-transparent px-2 py-1 text-sm"
        style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}
      />
    </label>
  );
}
