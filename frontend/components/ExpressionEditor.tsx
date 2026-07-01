"use client";

import { Brackets, Plus, Trash2 } from "lucide-react";
import useSWR from "swr";

import InstrumentSelector from "@/components/InstrumentSelector";
import { listInstruments, type InstrumentMetadataResponse } from "@/lib/api";

export type SignalExpression = Record<string, unknown>;

type PrimitiveType = "price_above" | "price_below" | "rapid_rise" | "rapid_drop" | "volume_multiple";

const primitiveLabels: Record<PrimitiveType, string> = {
  price_above: "價格高於門檻",
  price_below: "價格低於門檻",
  rapid_rise: "指定時間內快速上漲",
  rapid_drop: "指定時間內快速下跌",
  volume_multiple: "成交量放大",
};

const defaultPrimitive = (): SignalExpression => ({ type: "price_above", symbol: "self", value: 0 });

function isComposite(expression: SignalExpression): boolean {
  return expression.op === "and" || expression.op === "or";
}

function conditions(expression: SignalExpression): SignalExpression[] {
  return Array.isArray(expression.conditions)
    ? expression.conditions.filter((item): item is SignalExpression => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function asText(value: unknown, fallback = ""): string {
  return value === undefined || value === null ? fallback : String(value);
}

function primitive(type: PrimitiveType, current: SignalExpression): SignalExpression {
  const symbol = asText(current.symbol, "self");
  if (type === "price_above" || type === "price_below") return { type, symbol, value: Number(current.value ?? 0) };
  if (type === "rapid_rise" || type === "rapid_drop") {
    return { type, symbol, window_seconds: Number(current.window_seconds ?? 120), change_pct: Number(current.change_pct ?? 3) };
  }
  return { type, symbol, timeframe: asText(current.timeframe, "1m"), multiplier: Number(current.multiplier ?? 2) };
}

function NumberField({ label, value, onChange, suffix }: { label: string; value: unknown; onChange: (value: number) => void; suffix?: string }) {
  return (
    <label className="field compact-field">
      <span>{label}</span>
      <span className="input-with-suffix">
        <input type="number" step="any" value={asText(value, "0")} onChange={(event) => onChange(Number(event.target.value))} />
        {suffix && <small>{suffix}</small>}
      </span>
    </label>
  );
}

function Node({ expression, onChange, onRemove, depth, instruments, catalogStale }: {
  expression: SignalExpression;
  onChange: (next: SignalExpression) => void;
  onRemove?: () => void;
  depth: number;
  instruments: InstrumentMetadataResponse[];
  catalogStale: boolean;
}) {
  if (isComposite(expression)) {
    const items = conditions(expression);
    const op = expression.op === "or" ? "or" : "and";
    const updateItem = (index: number, next: SignalExpression) => {
      const updated = [...items];
      updated[index] = next;
      onChange({ op, conditions: updated });
    };
    const removeItem = (index: number) => {
      const updated = items.filter((_, itemIndex) => itemIndex !== index);
      onChange(updated.length === 1 ? updated[0] : { op, conditions: updated });
    };
    return (
      <div className="expression-group" data-depth={depth}>
        <div className="expression-toolbar">
          <span className="group-bracket"><Brackets size={16} /> 條件群組</span>
          <div className="segmented" aria-label="群組運算子">
            <button type="button" className={op === "and" ? "selected" : ""} onClick={() => onChange({ op: "and", conditions: items })}>AND</button>
            <button type="button" className={op === "or" ? "selected" : ""} onClick={() => onChange({ op: "or", conditions: items })}>OR</button>
          </div>
          {onRemove && <button type="button" className="icon-button danger-ghost" aria-label="移除群組" onClick={onRemove}><Trash2 size={16} /></button>}
        </div>
        <div className="expression-children">
          {items.map((item, index) => (
            <div className="expression-child" key={index}>
              {index > 0 && <span className="operator-chip">{op.toUpperCase()}</span>}
              <Node expression={item} onChange={(next) => updateItem(index, next)} onRemove={() => removeItem(index)} depth={depth + 1} instruments={instruments} catalogStale={catalogStale} />
            </div>
          ))}
        </div>
        <div className="expression-actions">
          <button type="button" className="btn btn-outline" onClick={() => onChange({ op, conditions: [...items, defaultPrimitive()] })}><Plus size={15} /> 新增條件</button>
          {depth < 4 && <button type="button" className="btn btn-outline" onClick={() => onChange({ op, conditions: [...items, { op: "and", conditions: [defaultPrimitive(), defaultPrimitive()] }] })}><Brackets size={15} /> 新增群組</button>}
        </div>
      </div>
    );
  }

  const type = (Object.keys(primitiveLabels).includes(asText(expression.type)) ? expression.type : "price_above") as PrimitiveType;
  const set = (key: string, value: unknown) => onChange({ ...expression, [key]: value });
  return (
    <div className="expression-condition">
      <label className="field compact-field grow-field">
        <span>條件</span>
        <select value={type} onChange={(event) => onChange(primitive(event.target.value as PrimitiveType, expression))}>
          {Object.entries(primitiveLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select>
      </label>
      <label className="field compact-field grow-field">
        <span>市場</span>
        <InstrumentSelector
          instruments={instruments}
          stale={catalogStale}
          includeSelf
          value={[asText(expression.symbol, "self")]}
          onChange={(next) => set("symbol", next[0] ?? "self")}
        />
      </label>
      {(type === "price_above" || type === "price_below") && <NumberField label="價格" value={expression.value} onChange={(value) => set("value", value)} />}
      {(type === "rapid_rise" || type === "rapid_drop") && <>
        <NumberField label="漲跌幅" value={expression.change_pct} suffix="%" onChange={(value) => set("change_pct", value)} />
        <NumberField label="時間內" value={expression.window_seconds} suffix="秒" onChange={(value) => set("window_seconds", value)} />
      </>}
      {type === "volume_multiple" && <>
        <label className="field compact-field"><span>週期</span><input value={asText(expression.timeframe, "1m")} onChange={(event) => set("timeframe", event.target.value)} /></label>
        <NumberField label="倍數" value={expression.multiplier} suffix="×" onChange={(value) => set("multiplier", value)} />
      </>}
      {onRemove && <button type="button" className="icon-button danger-ghost condition-remove" aria-label="移除條件" onClick={onRemove}><Trash2 size={16} /></button>}
    </div>
  );
}

export function describeExpression(expression: SignalExpression): string {
  if (isComposite(expression)) {
    const joiner = ` ${String(expression.op).toUpperCase()} `;
    return `(${conditions(expression).map(describeExpression).join(joiner)})`;
  }
  const type = asText(expression.type);
  const symbol = asText(expression.symbol, "self");
  if (type === "price_above") return `${symbol} 價格 > ${asText(expression.value)}`;
  if (type === "price_below") return `${symbol} 價格 < ${asText(expression.value)}`;
  if (type === "rapid_rise") return `${symbol} 在 ${asText(expression.window_seconds)} 秒內上漲 ${asText(expression.change_pct)}%`;
  if (type === "rapid_drop") return `${symbol} 在 ${asText(expression.window_seconds)} 秒內下跌 ${asText(expression.change_pct)}%`;
  if (type === "volume_multiple") return `${symbol} ${asText(expression.timeframe)} 成交量 ≥ 基準 ${asText(expression.multiplier)} 倍`;
  return "條件尚未完成";
}

export default function ExpressionEditor({ value, onChange, label = "規則運算式" }: { value: SignalExpression; onChange: (value: SignalExpression) => void; label?: string }) {
  const safeValue = Object.keys(value).length ? value : defaultPrimitive();
  const catalog = useSWR("instrument-metadata", listInstruments);
  return (
    <div className="expression-editor">
      <div className="expression-heading">
        <div><strong>{label}</strong><p>{describeExpression(safeValue)}</p></div>
        {!isComposite(safeValue) && <button type="button" className="btn btn-outline" onClick={() => onChange({ op: "and", conditions: [safeValue, defaultPrimitive()] })}><Brackets size={15} /> 加入 AND／OR</button>}
      </div>
      {catalog.error && <div className="inline-warning">商品資料尚未快取；請先更新 OKX 商品資料，規則目標目前只能使用 self。</div>}
      <Node expression={safeValue} onChange={onChange} depth={0} instruments={catalog.data?.items ?? []} catalogStale={catalog.data?.stale ?? true} />
    </div>
  );
}
