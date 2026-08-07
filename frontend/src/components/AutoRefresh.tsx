"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Re-runs the server component's own `cache: "no-store"` fetches on a
 * timer via `router.refresh()` — no client-side data-fetching duplicated
 * here. Without this, a page loaded once and left open never updates:
 * live_loop.py marks positions every ~60s, but nothing told an open tab
 * to ask for the new numbers (user-reported: a position's "live" mark
 * was actually >1h stale). No-ops harmlessly on the static export (no
 * server to refresh against).
 */
export function AutoRefresh({ intervalMs = 30_000 }: { intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    const id = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);

  return null;
}
