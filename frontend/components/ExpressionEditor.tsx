"use client";

import { useMemo, useState } from "react";
import { Ban, Brackets, Plus, Trash2 } from "lucide-react";
import useSWR from "swr";

import InstrumentSelector from "@/components/InstrumentSelector";
import {
  getSupportResistanceAnalysis,
  listInstruments,
  type InstrumentMetadataResponse,
  type SupportResistanceAnalysisResponse,
} from "@/lib/api";

export type SignalExpression = Record<string, unknown>;

type PrimitiveType = "price_above" | "price_below" | "rapid_rise" | "rapid_drop" | "volume_multiple" | "boundary_approach";
type SupportResistanceLevel = NonNullable<SupportResistanceAnalysisResponse["levels"]>[number];

const analysisBars = ["1m", "5m", "15m", "1H", "4H", "1D"];

// Must match src.trading.signal_context.SUPPORTED_BARS (backend rejects any other value).
const volumeTimeframes = ["1s", "1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D", "1W"];

const primitiveLabels: Record<PrimitiveType, string> = {
  price_above: "價格高於門檻",
  price_below: "價格低於門檻",
  rapid_rise: "指定時間內快速上漲",
  rapid_drop: "指定時間內快速下跌",
  volume_multiple: "成交量放大",
  boundary_approach: "接近但未突破支撐／壓力",
};

const defaultPrimitive = (): SignalExpression => ({ type: "price_above", symbol: "self", value: 0 });

function isComposite(expression: SignalExpression): boolean {
  return expression.op === "and" || expression.op === "or" || expression.op === "not";
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
  if (type === "boundary_approach") {
    const side = current.side === "resistance" ? "resistance" : "support";
    return { type, symbol, boundary: Number(current.boundary ?? 0), side, tolerance_pct: Number(current.tolerance_pct ?? 0.5) };
  }
  return { type, symbol, timeframe: volumeTimeframes.includes(asText(current.timeframe)) ? asText(current.timeframe) : "1m", multiplier: Number(current.multiplier ?? 2) };
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

function levelLabel(level: SupportResistanceLevel, pricePrecision?: number | null): string {
  const kind = level.kind === "support" ? "支撐" : "壓力";
  const price = Number(level.price).toLocaleString("zh-TW", { maximumFractionDigits: pricePrecision ?? 8 });
  const score = Math.round(Number(level.score ?? 0) * 100);
  return `${kind} ${price} · ${score}% · ${level.touches} 次`;
}

function PriceFieldWithAnalysis({ expression, setValue, analysisSymbols }: {
  expression: SignalExpression;
  setValue: (value: number) => void;
  analysisSymbols: string[];
}) {
  const [bar, setBar] = useState("15m");
  const symbol = asText(expression.symbol, "self");
  const concreteSymbol = symbol === "self"
    ? analysisSymbols.length === 1 ? analysisSymbols[0] : ""
    : symbol;
  const analysis = useSWR(
    concreteSymbol ? ["expression-support-resistance", concreteSymbol, bar] : null,
    () => getSupportResistanceAnalysis(concreteSymbol, { bar, limit: 200 }),
    { refreshInterval: 30_000 },
  );
  const preferredKind = expression.type === "price_below" ? "support" : "resistance";
  const levels = useMemo(() => {
    const raw = analysis.data?.levels ?? [];
    return [...raw]
      .filter((level) => level.state === "active")
      .sort((left, right) => {
        if (left.kind !== right.kind) return left.kind === preferredKind ? -1 : 1;
        return Number(right.score ?? 0) - Number(left.score ?? 0);
      })
      .slice(0, 12);
  }, [analysis.data?.levels, preferredKind]);
  return (
    <div className="price-analysis-field">
      <NumberField label="價格" value={expression.value} onChange={setValue} />
      <label className="field compact-field">
        <span>分析週期</span>
        <select value={bar} onChange={(event) => setBar(event.target.value)}>
          {analysisBars.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <label className="field level-picker-field">
        <span>支撐／壓力</span>
        <select
          disabled={!concreteSymbol || analysis.isLoading || Boolean(analysis.error) || levels.length === 0}
          value=""
          onChange={(event) => {
            const level = levels[Number(event.target.value)];
            if (level) setValue(Number(level.price));
          }}
        >
          <option value="">{analysis.isLoading ? "讀取市場分析…" : levels.length ? "選擇分析價位" : "沒有可用層級"}</option>
          {levels.map((level, index) => (
            <option key={`${level.kind}-${level.price}-${level.latest_touch_at}`} value={index}>
              {levelLabel(level, analysis.data?.price_precision)}
            </option>
          ))}
        </select>
      </label>
      {symbol === "self" && analysisSymbols.length !== 1 && (
        <div className="inline-warning expression-level-warning">self 需要唯一交易商品才會載入支撐／壓力；請改選明確市場，或讓策略只選一個商品。</div>
      )}
      {analysis.error && <div className="inline-warning expression-level-warning">市場分析 API 無法使用；仍可手動輸入價格。</div>}
      {analysis.data && analysis.data.status !== "fresh" && <div className="inline-warning expression-level-warning">此分析目前為 {analysis.data.status}；選入後仍只是填入價格，請自行審閱。</div>}
    </div>
  );
}

function BoundaryApproachField({ expression, onChange, analysisSymbols }: {
  expression: SignalExpression;
  onChange: (next: SignalExpression) => void;
  analysisSymbols: string[];
}) {
  const [bar, setBar] = useState("15m");
  const symbol = asText(expression.symbol, "self");
  const concreteSymbol = symbol === "self"
    ? analysisSymbols.length === 1 ? analysisSymbols[0] : ""
    : symbol;
  const side = expression.side === "resistance" ? "resistance" : "support";
  const analysis = useSWR(
    concreteSymbol ? ["expression-boundary-approach", concreteSymbol, bar] : null,
    () => getSupportResistanceAnalysis(concreteSymbol, { bar, limit: 200 }),
    { refreshInterval: 30_000 },
  );
  const levels = useMemo(() => {
    const raw = analysis.data?.levels ?? [];
    return [...raw]
      .filter((level) => level.state === "active" && level.kind === side)
      .sort((left, right) => Number(right.score ?? 0) - Number(left.score ?? 0))
      .slice(0, 12);
  }, [analysis.data?.levels, side]);
  return (
    <div className="price-analysis-field">
      <label className="field compact-field">
        <span>邊界方向</span>
        <select value={side} onChange={(event) => onChange({ ...expression, side: event.target.value })}>
          <option value="support">支撐（由上往下接近）</option>
          <option value="resistance">壓力（由下往上接近）</option>
        </select>
      </label>
      <NumberField label="邊界價格" value={expression.boundary} onChange={(value) => onChange({ ...expression, boundary: value })} />
      <NumberField label="容忍範圍" value={expression.tolerance_pct} suffix="%" onChange={(value) => onChange({ ...expression, tolerance_pct: value })} />
      <label className="field compact-field">
        <span>分析週期</span>
        <select value={bar} onChange={(event) => setBar(event.target.value)}>
          {analysisBars.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <label className="field level-picker-field">
        <span>支撐／壓力</span>
        <select
          disabled={!concreteSymbol || analysis.isLoading || Boolean(analysis.error) || levels.length === 0}
          value=""
          onChange={(event) => {
            const level = levels[Number(event.target.value)];
            if (level) onChange({ ...expression, boundary: Number(level.price) });
          }}
        >
          <option value="">{analysis.isLoading ? "讀取市場分析…" : levels.length ? "選擇分析價位" : "沒有可用層級"}</option>
          {levels.map((level, index) => (
            <option key={`${level.kind}-${level.price}-${level.latest_touch_at}`} value={index}>
              {levelLabel(level, analysis.data?.price_precision)}
            </option>
          ))}
        </select>
      </label>
      {symbol === "self" && analysisSymbols.length !== 1 && (
        <div className="inline-warning expression-level-warning">self 需要唯一交易商品才會載入支撐／壓力；請改選明確市場，或讓策略只選一個商品。</div>
      )}
      {analysis.error && <div className="inline-warning expression-level-warning">市場分析 API 無法使用；仍可手動輸入邊界價格。</div>}
      {analysis.data && analysis.data.status !== "fresh" && <div className="inline-warning expression-level-warning">此分析目前為 {analysis.data.status}；選入後仍只是填入價格，請自行審閱。</div>}
    </div>
  );
}

function Node({ expression, onChange, onRemove, depth, instruments, catalogStale, analysisSymbols }: {
  expression: SignalExpression;
  onChange: (next: SignalExpression) => void;
  onRemove?: () => void;
  depth: number;
  instruments: InstrumentMetadataResponse[];
  catalogStale: boolean;
  analysisSymbols: string[];
}) {
  if (isComposite(expression)) {
    const items = conditions(expression);
    const op = expression.op === "or" ? "or" : expression.op === "not" ? "not" : "and";
    const updateItem = (index: number, next: SignalExpression) => {
      const updated = [...items];
      updated[index] = next;
      onChange({ op, conditions: updated });
    };
    const removeItem = (index: number) => {
      const updated = items.filter((_, itemIndex) => itemIndex !== index);
      onChange(updated.length === 1 ? updated[0] : { op, conditions: updated });
    };
    const setOp = (nextOp: "and" | "or" | "not") => {
      if (nextOp === "not") {
        // Wrap rather than drop conditions when switching a multi-condition AND/OR group to NOT.
        onChange({
          op: "not",
          conditions: items.length > 1 ? [{ op: op === "not" ? "and" : op, conditions: items }] : [items[0] ?? defaultPrimitive()],
        });
        return;
      }
      onChange({ op: nextOp, conditions: items.length ? items : [defaultPrimitive()] });
    };
    return (
      <div className="expression-group" data-depth={depth}>
        <div className="expression-toolbar">
          <span className="group-bracket"><Brackets size={16} /> {op === "not" ? "NOT 條件" : "條件群組"}</span>
          <div className="segmented" aria-label="群組運算子">
            <button type="button" className={op === "and" ? "selected" : ""} onClick={() => setOp("and")}>AND</button>
            <button type="button" className={op === "or" ? "selected" : ""} onClick={() => setOp("or")}>OR</button>
            <button type="button" className={op === "not" ? "selected" : ""} onClick={() => setOp("not")}>NOT</button>
          </div>
          {onRemove && <button type="button" className="icon-button danger-ghost" aria-label="移除群組" onClick={onRemove}><Trash2 size={16} /></button>}
        </div>
        <div className="expression-children">
          {op === "not" ? (
            <div className="expression-child">
              <Node
                expression={items[0] ?? defaultPrimitive()}
                onChange={(next) => onChange({ op: "not", conditions: [next] })}
                depth={depth + 1}
                instruments={instruments}
                catalogStale={catalogStale}
                analysisSymbols={analysisSymbols}
              />
            </div>
          ) : (
            items.map((item, index) => (
              <div className="expression-child" key={index}>
                {index > 0 && <span className="operator-chip">{op.toUpperCase()}</span>}
                <Node expression={item} onChange={(next) => updateItem(index, next)} onRemove={() => removeItem(index)} depth={depth + 1} instruments={instruments} catalogStale={catalogStale} analysisSymbols={analysisSymbols} />
              </div>
            ))
          )}
        </div>
        {op !== "not" && (
          <div className="expression-actions">
            <button type="button" className="btn btn-outline" onClick={() => onChange({ op, conditions: [...items, defaultPrimitive()] })}><Plus size={15} /> 新增條件</button>
            {depth < 4 && <button type="button" className="btn btn-outline" onClick={() => onChange({ op, conditions: [...items, { op: "and", conditions: [defaultPrimitive(), defaultPrimitive()] }] })}><Brackets size={15} /> 新增群組</button>}
          </div>
        )}
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
      {(type === "price_above" || type === "price_below") && (
        <PriceFieldWithAnalysis expression={expression} setValue={(value) => set("value", value)} analysisSymbols={analysisSymbols} />
      )}
      {(type === "rapid_rise" || type === "rapid_drop") && <>
        <NumberField label="漲跌幅" value={expression.change_pct} suffix="%" onChange={(value) => set("change_pct", value)} />
        <NumberField label="時間內" value={expression.window_seconds} suffix="秒" onChange={(value) => set("window_seconds", value)} />
      </>}
      {type === "volume_multiple" && <>
        <label className="field compact-field">
          <span>週期</span>
          <select value={volumeTimeframes.includes(asText(expression.timeframe)) ? asText(expression.timeframe) : "1m"} onChange={(event) => set("timeframe", event.target.value)}>
            {volumeTimeframes.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <NumberField label="倍數" value={expression.multiplier} suffix="×" onChange={(value) => set("multiplier", value)} />
      </>}
      {type === "boundary_approach" && (
        <BoundaryApproachField expression={expression} onChange={onChange} analysisSymbols={analysisSymbols} />
      )}
      {onRemove && <button type="button" className="icon-button danger-ghost condition-remove" aria-label="移除條件" onClick={onRemove}><Trash2 size={16} /></button>}
    </div>
  );
}

export function describeExpression(expression: SignalExpression): string {
  if (isComposite(expression)) {
    if (expression.op === "not") {
      const inner = conditions(expression)[0];
      return `NOT (${inner ? describeExpression(inner) : ""})`;
    }
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
  if (type === "boundary_approach") {
    const sideLabel = expression.side === "resistance" ? "壓力" : "支撐";
    return `${symbol} 接近${sideLabel} ${asText(expression.boundary)}（容忍 ${asText(expression.tolerance_pct)}%）但未突破`;
  }
  return "條件尚未完成";
}

export default function ExpressionEditor({ value, onChange, label = "規則運算式", analysisSymbols = [] }: { value: SignalExpression; onChange: (value: SignalExpression) => void; label?: string; analysisSymbols?: string[] }) {
  const safeValue = Object.keys(value).length ? value : defaultPrimitive();
  const catalog = useSWR("instrument-metadata", listInstruments);
  const concreteAnalysisSymbols = analysisSymbols.filter((item) => item && item !== "self");
  return (
    <div className="expression-editor">
      <div className="expression-heading">
        <div><strong>{label}</strong><p>{describeExpression(safeValue)}</p></div>
        <div className="expression-heading-actions">
          {!isComposite(safeValue) && <button type="button" className="btn btn-outline" onClick={() => onChange({ op: "and", conditions: [safeValue, defaultPrimitive()] })}><Brackets size={15} /> 加入 AND／OR</button>}
          <button type="button" className="btn btn-outline" onClick={() => onChange({ op: "not", conditions: [safeValue] })}><Ban size={15} /> 加入 NOT</button>
        </div>
      </div>
      {catalog.error && <div className="inline-warning">商品資料尚未快取；請先更新 OKX 商品資料，規則目標目前只能使用 self。</div>}
      <Node expression={safeValue} onChange={onChange} depth={0} instruments={catalog.data?.items ?? []} catalogStale={catalog.data?.stale ?? true} analysisSymbols={concreteAnalysisSymbols} />
    </div>
  );
}
