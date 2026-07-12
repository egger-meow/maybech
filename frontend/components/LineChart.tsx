"use client";

import { useState, type MouseEvent } from "react";

export type ChartPoint = { value: number; shortLabel: string; fullLabel: string };

export function shortDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("zh-TW", { month: "2-digit", day: "2-digit" });
}

export function fullDateTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString("zh-TW");
}

export default function LineChart({
  points,
  height = 150,
  minDomain,
  maxDomain,
  ariaLabel,
  valueFormat,
}: {
  points: ChartPoint[];
  height?: number;
  minDomain?: number;
  maxDomain?: number;
  ariaLabel: string;
  valueFormat: (value: number) => string;
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  if (points.length < 2) return null;

  const width = 640;
  const values = points.map((point) => point.value);
  const min = minDomain ?? Math.min(...values);
  const max = maxDomain ?? Math.max(...values);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);
  const yFor = (value: number) => height - ((value - min) / span) * (height - 18) - 9;
  const xFor = (index: number) => index * stepX;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${xFor(index).toFixed(1)},${yFor(point.value).toFixed(1)}`)
    .join(" ");
  const area = `${path} L${width},${height - 4} L0,${height - 4} Z`;
  const first = points[0];
  const last = points[points.length - 1];
  const hovered = hoverIndex != null ? points[hoverIndex] : null;

  const handleMove = (event: MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return;
    const relativeX = ((event.clientX - rect.left) / rect.width) * width;
    const index = Math.max(0, Math.min(points.length - 1, Math.round(relativeX / stepX)));
    setHoverIndex(index);
  };

  return (
    <div className="macro-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <path d={area} className="macro-chart-area" />
        <path d={path} fill="none" strokeWidth={2.5} className="macro-chart-line" />
        {hoverIndex != null && (
          <>
            <line x1={xFor(hoverIndex)} x2={xFor(hoverIndex)} y1={0} y2={height} className="macro-chart-crosshair" />
            <circle cx={xFor(hoverIndex)} cy={yFor(points[hoverIndex].value)} r={4} className="macro-chart-dot" />
          </>
        )}
      </svg>
      {hovered && hoverIndex != null && (
        <div className="macro-chart-tooltip" style={{ left: `${(xFor(hoverIndex) / width) * 100}%` }}>
          <strong>{valueFormat(hovered.value)}</strong>
          <span>{hovered.fullLabel}</span>
        </div>
      )}
      <div className="macro-chart-axis">
        <span>{first.shortLabel}</span>
        <span>{last.shortLabel}</span>
      </div>
    </div>
  );
}
