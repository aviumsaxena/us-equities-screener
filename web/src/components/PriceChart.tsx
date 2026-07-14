import { createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { PriceBar } from "../api";

interface Props {
  bars: PriceBar[];
}

/** Candlestick chart over daily_prices. History is currently ~100 trading days
 *  (free-tier price vendor, see ARCHITECTURE §6), so there's no range selector
 *  yet — it would have nothing to select between. */
export function PriceChart({ bars }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 320,
      layout: { background: { color: "transparent" }, textColor: "#5b6472" },
      grid: {
        vertLines: { color: "rgba(0,0,0,0.05)" },
        horzLines: { color: "rgba(0,0,0,0.05)" },
      },
      rightPriceScale: { borderColor: "rgba(0,0,0,0.1)" },
      timeScale: { borderColor: "rgba(0,0,0,0.1)" },
    });
    chartRef.current = chart;

    const series = chart.addCandlestickSeries({
      upColor: "#16a34a",
      downColor: "#dc2626",
      borderUpColor: "#16a34a",
      borderDownColor: "#dc2626",
      wickUpColor: "#16a34a",
      wickDownColor: "#dc2626",
    });

    series.setData(
      bars
        // a bar missing OHLC can't be drawn as a candle; the close-only rows
        // that produced it are still valid for the metrics, just not the chart
        .filter((b) => b.open != null && b.high != null && b.low != null)
        .map((b) => ({
          time: b.dt as unknown as UTCTimestamp, // 'YYYY-MM-DD' is a valid time for daily bars
          open: b.open as number,
          high: b.high as number,
          low: b.low as number,
          close: b.close,
        })),
    );
    chart.timeScale().fitContent();

    const onResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [bars]);

  if (bars.length === 0) return <p className="muted">No price history for this company.</p>;
  return <div className="chart" ref={containerRef} />;
}
