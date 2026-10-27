import type { ECharts } from "echarts/core";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { EChartsOption } from "./echarts";

/**
 * Draws an ECharts option into a box of a fixed height (the height includes the axis labels, so there is no inner scroll) and keeps it
 * the size of the box. The library is loaded on first use. The option comes from a builder that already follows the chart standards;
 * this component only owns the instance: create, update, resize, dispose.
 */
export function Chart({ option, height, className }: { option: EChartsOption; height: number; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const instance = useRef<ECharts | null>(null);
  const latest = useRef(option);
  useEffect(() => {
    latest.current = option;
  });
  const drawn = useRef("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let observer: ResizeObserver | null = null;
    void import("./echarts").then(({ echarts }) => {
      if (cancelled || !box.current) return;
      const chart = echarts.init(box.current, undefined, { renderer: "svg" });
      instance.current = chart;
      chart.setOption(latest.current, true);
      drawn.current = JSON.stringify(latest.current);
      observer = new ResizeObserver(() => chart.resize());
      observer.observe(box.current);
      setReady(true);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      instance.current?.dispose();
      instance.current = null;
    };
  }, []);

  // A builder returns a new object on every render; redraw only when what it describes has changed (functions are not part of the comparison).
  const signature = JSON.stringify(option);
  useEffect(() => {
    if (!ready || drawn.current === signature) return;
    drawn.current = signature;
    instance.current?.setOption(latest.current, true);
  }, [signature, ready]);

  // The drawing is decorative for assistive technology: the summary and the table next to it carry the same information.
  return <div ref={box} data-chart="" aria-hidden="true" className={cn("w-full", className)} style={{ height }} />;
}
