"use client";

import { useEffect, useRef } from "react";
import { createChart, AreaSeries, type IChartApi } from "lightweight-charts";
import type { EquityPoint } from "@/lib/types";

/** TradingView Lightweight Charts (ARCHITECTURE.md §16) — its own
 * crosshair + tooltip ships built in, satisfying the dataviz skill's
 * "hover layer by default" step for free. One series (equity), so no
 * legend box is needed (the card title names it — skill's own rule for
 * a lone series). */
export function EquityChart({ points }: { points: EquityPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const isDark = document.documentElement.getAttribute("data-theme") === "dark"
      || (window.matchMedia("(prefers-color-scheme: dark)").matches
        && document.documentElement.getAttribute("data-theme") !== "light");
    const textColor = isDark ? "#c3c2b7" : "#52514e";
    const lineColor = isDark ? "#3987e5" : "#2a78d6";

    const chart = createChart(containerRef.current, {
      height: 260,
      layout: { textColor, background: { color: "transparent" } },
      grid: {
        vertLines: { color: isDark ? "#242422" : "#f3f2ef" },
        horzLines: { color: isDark ? "#242422" : "#f3f2ef" },
      },
      timeScale: { timeVisible: true, borderColor: isDark ? "#34342f" : "#e4e2dd" },
      rightPriceScale: { borderColor: isDark ? "#34342f" : "#e4e2dd" },
    });
    chartRef.current = chart;

    const series = chart.addSeries(AreaSeries, {
      lineColor,
      topColor: `${lineColor}33`,
      bottomColor: `${lineColor}00`,
      lineWidth: 2,
    });
    series.setData(
      points.map((p) => ({
        time: Math.floor(new Date(p.ts).getTime() / 1000) as never,
        value: p.equity,
      })),
    );
    chart.timeScale().fitContent();

    const resize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="flex h-[260px] items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
        No equity snapshots in this window yet.
      </div>
    );
  }

  return <div ref={containerRef} className="w-full" />;
}
