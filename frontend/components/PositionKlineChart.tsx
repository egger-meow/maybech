"use client";

import { useEffect, useRef } from "react";
import {
  dispose,
  init,
  registerOverlay,
  type Chart,
  type KLineData,
  type OverlayFigure,
} from "klinecharts";

import { formatPrice } from "@/lib/price-format";
import type { LogicalPositionChartResponse } from "@/lib/api";

const THEME_CHANGE_EVENT = "maybech-theme-change";

const OVERLAY_COLORS: Record<string, string> = {
  entry: "#3b82f6",
  current: "#8b5cf6",
  stop_loss: "#ef4444",
  take_profit: "#10b981",
  break_even: "#f59e0b",
  trailing: "#06b6d4",
  execution: "#94a3b8",
};

const LEVEL_LINE_OVERLAY = "position-level-line";
const EVENT_MARKER_OVERLAY = "position-event-marker";

type OverlayExtendData = { color?: string; text?: string };

// registerOverlay mutates a module-level registry inside klinecharts itself, so this
// only needs to happen once per page load, not once per chart instance.
let overlaysRegistered = false;

function registerPositionOverlays() {
  if (overlaysRegistered) return;
  overlaysRegistered = true;

  registerOverlay({
    name: LEVEL_LINE_OVERLAY,
    totalStep: 1,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ overlay, coordinates, bounding }): OverlayFigure[] => {
      const point = coordinates[0];
      if (!point) return [];
      const { color = "#64748b", text = "" } = (overlay.extendData ?? {}) as OverlayExtendData;
      return [
        {
          type: "line",
          attrs: { coordinates: [{ x: 0, y: point.y }, { x: bounding.width, y: point.y }] },
          styles: { style: "dashed", dashedValue: [6, 4], size: 1.5, color },
          ignoreEvent: true,
        },
        {
          type: "text",
          attrs: { x: bounding.width - 4, y: point.y, text, align: "right", baseline: "bottom" },
          styles: {
            color: "#fff",
            backgroundColor: color,
            size: 11,
            weight: "600",
            paddingLeft: 4,
            paddingRight: 4,
            paddingTop: 2,
            paddingBottom: 2,
            borderRadius: 3,
          },
          ignoreEvent: true,
        },
      ];
    },
  });

  registerOverlay({
    name: EVENT_MARKER_OVERLAY,
    totalStep: 1,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ overlay, coordinates }): OverlayFigure[] => {
      const point = coordinates[0];
      if (!point) return [];
      const { color = "#94a3b8", text = "" } = (overlay.extendData ?? {}) as OverlayExtendData;
      return [
        {
          type: "circle",
          attrs: { x: point.x, y: point.y, r: 4 },
          styles: { style: "fill", color, borderColor: "#0f172a", borderSize: 1.5 },
          ignoreEvent: true,
        },
        {
          type: "text",
          attrs: { x: point.x, y: point.y - 10, text, align: "center", baseline: "bottom" },
          styles: { color, size: 10, weight: "600" },
          ignoreEvent: true,
        },
      ];
    },
  });
}

function readThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    grid: read("--border-color", "#334155"),
    text: read("--text-muted", "#94a3b8"),
    up: "#10b981",
    down: "#ef4444",
  };
}

function applyTheme(chart: Chart) {
  const colors = readThemeColors();
  chart.setStyles({
    grid: { horizontal: { color: colors.grid }, vertical: { color: colors.grid } },
    candle: {
      bar: {
        upColor: colors.up,
        downColor: colors.down,
        noChangeColor: colors.text,
        upBorderColor: colors.up,
        downBorderColor: colors.down,
        noChangeBorderColor: colors.text,
        upWickColor: colors.up,
        downWickColor: colors.down,
        noChangeWickColor: colors.text,
      },
    },
    xAxis: { axisLine: { color: colors.grid }, tickLine: { color: colors.grid }, tickText: { color: colors.text } },
    yAxis: { axisLine: { color: colors.grid }, tickLine: { color: colors.grid }, tickText: { color: colors.text } },
    crosshair: {
      horizontal: { line: { color: colors.text }, text: { backgroundColor: colors.text } },
      vertical: { line: { color: colors.text }, text: { backgroundColor: colors.text } },
    },
  });
}

function toKLineData(candles: LogicalPositionChartResponse["candles"]): KLineData[] {
  return (candles ?? []).map((candle) => ({
    timestamp: new Date(candle.timestamp).getTime(),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume,
  }));
}

export default function PositionKlineChart({
  chart,
  pricePrecision,
}: {
  chart: LogicalPositionChartResponse;
  pricePrecision?: number | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    registerPositionOverlays();
    const container = containerRef.current;
    if (!container) return;
    const instance = init(container, { locale: "zh-TW" });
    chartRef.current = instance;
    if (instance) applyTheme(instance);

    const resizeObserver = new ResizeObserver(() => instance?.resize());
    resizeObserver.observe(container);

    const onThemeChange = () => {
      if (chartRef.current) applyTheme(chartRef.current);
    };
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
    window.addEventListener("storage", onThemeChange);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
      window.removeEventListener("storage", onThemeChange);
      dispose(container);
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chartRef.current;
    if (!instance) return;
    instance.setPriceVolumePrecision(pricePrecision ?? 2, 4);
    const candles = toKLineData(chart.candles);
    instance.applyNewData(candles);

    instance.removeOverlay();
    (chart.overlays ?? []).forEach((overlay, index) => {
      const color = OVERLAY_COLORS[overlay.kind] ?? "#94a3b8";
      if (overlay.kind === "execution") {
        const timestamp = overlay.timestamp ? new Date(overlay.timestamp).getTime() : null;
        const dataIndex = timestamp == null ? -1 : candles.findIndex((candle) => candle.timestamp >= timestamp);
        if (dataIndex < 0) return;
        instance.createOverlay({
          name: EVENT_MARKER_OVERLAY,
          id: `event-${index}`,
          points: [{ dataIndex, value: overlay.price }],
          extendData: { color, text: overlay.label } satisfies OverlayExtendData,
          lock: true,
        });
        return;
      }
      instance.createOverlay({
        name: LEVEL_LINE_OVERLAY,
        id: `level-${overlay.kind}-${index}`,
        points: [{ value: overlay.price }],
        extendData: { color, text: `${overlay.label} ${formatPrice(overlay.price, pricePrecision)}` } satisfies OverlayExtendData,
        lock: true,
      });
    });
  }, [chart, pricePrecision]);

  if (!(chart.candles ?? []).length) {
    return <div className="empty-state">目前沒有可繪製的 K 線資料。</div>;
  }

  return (
    <div className="chart-wrap">
      <div ref={containerRef} className="kline-chart-canvas" role="img" aria-label={`${chart.inst_id} 部位 K 線與規則價位`} />
      <div className="chart-legend">
        {(chart.overlays ?? []).map((overlay, index) => (
          <span key={`${overlay.kind}-${index}`}>
            <i style={{ background: OVERLAY_COLORS[overlay.kind] ?? "#94a3b8" }} />
            {overlay.label}: {formatPrice(overlay.price, pricePrecision)}
          </span>
        ))}
      </div>
    </div>
  );
}
