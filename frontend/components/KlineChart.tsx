"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  DomPosition,
  LineType,
  dispose,
  init,
  registerOverlay,
  type Chart,
  type KLineData,
  type OverlayFigure,
} from "klinecharts";

import { formatPrice } from "@/lib/price-format";
import type { CandleResponse } from "@/lib/api";

// Deliberately decoupled from the backend's LogicalPositionChartResponse:
// this chart also renders research-only overlays (e.g. support/resistance)
// that have no position behind them, so its overlay "kind" is an open
// string, not the backend's position-protection-scoped literal union.
export type KlineChartOverlay = {
  kind: string;
  price: number;
  timestamp?: string | null;
  label: string;
  allocation_id?: string | null;
  // 0-1 evidence strength for research levels (support/resistance); drawn as a
  // circle marker sized by conviction so stronger levels read as more solid.
  evidenceScore?: number | null;
};

export type KlineChartData = {
  inst_id: string;
  bar: string;
  candles?: CandleResponse[];
  fetched_at: string;
  overlays?: KlineChartOverlay[];
};

const THEME_CHANGE_EVENT = "maybech-theme-change";

const OVERLAY_COLORS: Record<string, string> = {
  entry: "#3b82f6",
  current: "#8b5cf6",
  stop_loss: "#ef4444",
  take_profit: "#10b981",
  break_even: "#f59e0b",
  trailing: "#06b6d4",
  execution: "#94a3b8",
  support: "#10b981",
  resistance: "#ef4444",
  selected_level: "#3b82f6",
};

const LEVEL_LINE_OVERLAY = "position-level-line";
const EVENT_MARKER_OVERLAY = "position-event-marker";

// klinecharts' one built-in pane id for the main candle pane; not exported as a
// constant by the library, but stable across the public overlay/indicator API.
const CANDLE_PANE_ID = "candle_pane";
const VOLUME_PANE_ID = "position-volume-pane";

type OverlayExtendData = { color?: string; text?: string; evidenceScore?: number | null };

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
      const { color = "#64748b", text = "", evidenceScore } = (overlay.extendData ?? {}) as OverlayExtendData;
      const figures: OverlayFigure[] = [
        {
          type: "line",
          attrs: { coordinates: [{ x: 0, y: point.y }, { x: bounding.width, y: point.y }] },
          styles: { style: "dashed", dashedValue: [6, 4], size: 1.5, color },
          ignoreEvent: true,
        },
      ];
      // Evidence-weighted marker near the chart's left edge: radius scales with
      // conviction (score) so a glance at circle size ranks levels without
      // cluttering the price line/label on the right.
      if (evidenceScore != null && Number.isFinite(evidenceScore)) {
        const radius = 3 + Math.max(0, Math.min(1, evidenceScore)) * 7;
        figures.push({
          type: "circle",
          attrs: { x: 18, y: point.y, r: radius },
          styles: { style: "stroke_fill", color: `${color}33`, borderColor: color, borderSize: 1.5 },
          ignoreEvent: true,
        });
      }
      figures.push({
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
      });
      return figures;
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
    up: read("--accent-success", "#10b981"),
    down: read("--accent-danger", "#ef4444"),
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
      priceMark: {
        show: true,
        last: {
          show: true,
          upColor: colors.up,
          downColor: colors.down,
          noChangeColor: colors.text,
          line: { show: true, style: LineType.Dashed, dashedValue: [4, 4], size: 1 },
          text: {
            show: true,
            size: 12,
            weight: "600",
            paddingLeft: 6,
            paddingRight: 6,
            paddingTop: 3,
            paddingBottom: 3,
            borderRadius: 3,
          },
        },
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

function toKLineData(candles: KlineChartData["candles"]): KLineData[] {
  return (candles ?? []).map((candle) => ({
    timestamp: new Date(candle.timestamp).getTime(),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume,
  }));
}

// klinecharts only exposes drag-based price-axis zoom natively (mousedown+mousemove
// on the y-axis widget rescales its range; dblclick resets to auto-fit). There is no
// public API for wheel-based zoom on that axis. We bridge the gap by translating a
// wheel gesture over the y-axis into the same native drag sequence klinecharts already
// listens for, so scrolling there feels identical to TradingView's price-scale zoom.
function bindYAxisWheelZoom(chart: Chart): () => void {
  const yAxisDom = chart.getDom(CANDLE_PANE_ID, DomPosition.YAxis);
  if (!yAxisDom) return () => {};

  const handleWheel = (event: WheelEvent) => {
    if (Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
    event.preventDefault();
    const { clientX, clientY, pageY } = event;
    const zoomOut = event.deltaY > 0;
    const factor = zoomOut ? 1.08 : 1 / 1.08;
    const targetPageY = pageY * factor;
    const targetClientY = clientY + (targetPageY - pageY);

    const dispatch = (type: string, clientYValue: number) => {
      yAxisDom.dispatchEvent(
        new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          clientX,
          clientY: clientYValue,
          view: window,
        }),
      );
    };
    dispatch("mousedown", clientY);
    dispatch("mousemove", targetClientY);
    dispatch("mouseup", targetClientY);
  };

  yAxisDom.addEventListener("wheel", handleWheel, { passive: false });
  return () => yAxisDom.removeEventListener("wheel", handleWheel);
}

export default function KlineChart({
  chart,
  pricePrecision,
  showLegend = true,
  ariaLabel,
}: {
  chart: KlineChartData;
  pricePrecision?: number | null;
  showLegend?: boolean;
  ariaLabel?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    registerPositionOverlays();
    const container = containerRef.current;
    if (!container) return;
    const instance = init(container, { locale: "zh-TW" });
    chartRef.current = instance;
    if (!instance) return;

    applyTheme(instance);
    instance.createIndicator("VOL", false, { id: VOLUME_PANE_ID, height: 72 });
    const unbindWheelZoom = bindYAxisWheelZoom(instance);
    setReady(true);

    const resizeObserver = new ResizeObserver(() => instance.resize());
    resizeObserver.observe(container);

    const onThemeChange = () => applyTheme(instance);
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
    window.addEventListener("storage", onThemeChange);

    return () => {
      resizeObserver.disconnect();
      unbindWheelZoom();
      window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
      window.removeEventListener("storage", onThemeChange);
      dispose(container);
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chartRef.current;
    if (!instance || !ready) return;
    instance.setPriceVolumePrecision(pricePrecision ?? 2, 4);
    const candles = toKLineData(chart.candles);
    instance.applyNewData(candles);

    instance.removeOverlay();
    (chart.overlays ?? []).forEach((overlay, index) => {
      // The native last-price line/tag (set up in applyTheme) already conveys the
      // current price; drawing a second overlay line at the same value would just
      // stack a duplicate dashed line and label on top of it.
      if (overlay.kind === "current") return;
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
        extendData: {
          color,
          text: `${overlay.label} ${formatPrice(overlay.price, pricePrecision)}`,
          evidenceScore: overlay.evidenceScore,
        } satisfies OverlayExtendData,
        lock: true,
      });
    });
  }, [chart, pricePrecision, ready]);

  const currentOverlay = chart.overlays?.find((overlay) => overlay.kind === "current");
  const entryOverlay = chart.overlays?.find((overlay) => overlay.kind === "entry");
  const changePct = useMemo(() => {
    if (!currentOverlay || !entryOverlay || !entryOverlay.price) return null;
    return ((currentOverlay.price - entryOverlay.price) / entryOverlay.price) * 100;
  }, [currentOverlay, entryOverlay]);

  const hasCandles = (chart.candles ?? []).length > 0;

  return (
    <div className="chart-wrap">
      <div className="kline-header">
        <span className="kline-header-label">目前價</span>
        <strong className={changePct == null ? "" : changePct >= 0 ? "positive" : "negative"}>
          {formatPrice(currentOverlay?.price, pricePrecision)}
        </strong>
        {changePct != null && (
          <span className={changePct >= 0 ? "positive" : "negative"}>
            {changePct >= 0 ? "+" : ""}
            {changePct.toFixed(2)}%
          </span>
        )}
      </div>
      <div className="kline-chart-shell">
        <div ref={containerRef} className="kline-chart-canvas" role="img" aria-label={ariaLabel ?? `${chart.inst_id} K 線圖`} />
        {!hasCandles && <div className="empty-state kline-empty-overlay">目前沒有可繪製的 K 線資料。</div>}
      </div>
      {showLegend && (
        <div className="chart-legend">
          {(chart.overlays ?? [])
            .filter((overlay) => overlay.kind !== "current")
            .map((overlay, index) => (
              <span key={`${overlay.kind}-${index}`}>
                <i style={{ background: OVERLAY_COLORS[overlay.kind] ?? "#94a3b8" }} />
                {overlay.label}: {formatPrice(overlay.price, pricePrecision)}
              </span>
            ))}
        </div>
      )}
      <p className="kline-hint">捲動圖表縮放時間軸；捲動右側價格軸縮放價格；雙擊價格軸重設。</p>
    </div>
  );
}
