"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { AlertTriangle, Check, ChevronRight, CirclePlus, Power, Save, Trash2 } from "lucide-react";

import ExpressionEditor, { type SignalExpression } from "@/components/ExpressionEditor";
import InstrumentSelector from "@/components/InstrumentSelector";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import {
  ApiError,
  bootstrapSimulationInstruments,
  createStrategy,
  createStrategySignal,
  deleteStrategy,
  deleteStrategySignal,
  disableStrategy,
  enableStrategy,
  getMarketOverview,
  getRiskEnvelopeUsage,
  getRiskLimits,
  getLivePreflight,
  listPersistedStrategyDecisions,
  listInstruments,
  listStrategies,
  quoteInstrumentRisk,
  quoteInstrumentSize,
  quoteSwingStopLevel,
  refreshInstruments,
  updateStrategy,
  updateStrategySignal,
  validateSignal,
  type SignalExpression as PersistedSignalExpression,
  type InstrumentMetadataResponse,
  type StrategySummary,
} from "@/lib/api";
import { DEFAULT_ENTRY_FEE_RATE, DEFAULT_EXIT_FEE_RATE } from "@/lib/fee-defaults";

type CloseRule = { purpose: string; enabled: boolean; expression: SignalExpression; metadata?: Record<string, unknown> };
type Draft = {
  name: string;
  instruments: string[];
  side: "long" | "short";
  displayQuantities: Record<string, string>;
  referencePrices: Record<string, string>;
  slippagePercent: string;
  executionDelaySeconds: string;
  entrySignal: SignalExpression;
  closeRules: CloseRule[];
};

const blankDraft = (): Draft => ({
  name: "",
  instruments: [],
  side: "long",
  displayQuantities: {},
  referencePrices: {},
  slippagePercent: "0.5",
  executionDelaySeconds: "0",
  entrySignal: { type: "price_above", symbol: "self", value: 0 },
  closeRules: [
    { purpose: "stop_loss", enabled: true, expression: { type: "price_below", symbol: "self", value: 0 } },
    { purpose: "take_profit", enabled: true, expression: { type: "price_above", symbol: "self", value: 0 } },
  ],
});

function priceExpression(purpose: string, side: "long" | "short", value: number): SignalExpression {
  const above = (purpose === "take_profit" && side === "long") || (purpose === "stop_loss" && side === "short");
  return { type: above ? "price_above" : "price_below", symbol: "self", value };
}

function ruleDefinition(rule: CloseRule): Record<string, unknown> {
  return object(object(rule.metadata).rule_definition);
}

function ruleParameters(rule: CloseRule): Record<string, unknown> {
  const metadata = object(rule.metadata);
  return Object.keys(object(metadata.parameters)).length
    ? object(metadata.parameters)
    : object(ruleDefinition(rule).parameters);
}

function ruleStyle(rule: CloseRule): string {
  return String(ruleDefinition(rule).style ?? ((rule.purpose === "stop_loss" || rule.purpose === "take_profit") ? "absolute_price" : rule.purpose === "break_even" ? "break_even_threshold" : "signal_triggered"));
}

function withTypedRule(
  rule: CloseRule,
  style: string,
  parameters: Record<string, unknown>,
  action?: Record<string, unknown>,
): CloseRule {
  const metadata = object(rule.metadata);
  const definition = ruleDefinition(rule);
  const nextAction = action ?? object(definition.action);
  return {
    ...rule,
    metadata: {
      ...metadata,
      parameters,
      evidence: object(metadata.evidence),
      rule_definition: {
        ...definition,
        schema_version: 1,
        purpose: rule.purpose,
        style,
        enabled: rule.enabled,
        trigger: rule.expression,
        action: Object.keys(nextAction).length ? nextAction : rule.purpose === "break_even" ? { type: "amend_stop" } : { type: "close_position" },
        parameters,
        evidence: object(metadata.evidence),
      },
    },
  };
}

function defaultCloseRule(purpose: string, side: "long" | "short"): CloseRule {
  const expression = priceExpression(purpose, side, 1);
  if (purpose === "break_even") return withTypedRule({ purpose, enabled: true, expression }, "break_even_threshold", { activation_profit_pct: "0.01", entry_fee_rate: String(DEFAULT_ENTRY_FEE_RATE), exit_fee_rate: String(DEFAULT_EXIT_FEE_RATE), slippage_rate: "0.0005", lock_in_pct: "0" }, { type: "amend_stop" });
  if (purpose === "trailing") return withTypedRule({ purpose, enabled: true, expression: { type: side === "long" ? "price_above" : "price_below", symbol: "self", value: 1 } }, "trailing_threshold", { trailing_kind: "stop", activation_profit_pct: "0.03", distance_pct: "0.02", timeframe: "1m", stale_after_seconds: 90 }, { type: "amend_stop" });
  if (purpose === "stop_loss" || purpose === "take_profit") return withTypedRule({ purpose, enabled: true, expression }, "fixed_percent", { offset_pct: purpose === "stop_loss" ? "0.01" : "0.02" }, { type: "close_position" });
  return { purpose, enabled: true, expression };
}

function validateTypedCloseRules(rules: CloseRule[]): void {
  let initialReduction = 0;
  for (const rule of rules.filter((item) => item.enabled)) {
    const style = ruleStyle(rule);
    const parameters = ruleParameters(rule);
    const action = object(ruleDefinition(rule).action);
    if ((rule.purpose === "stop_loss" || rule.purpose === "take_profit") && style === "fixed_percent") {
      const offset = Number(parameters.offset_pct);
      if (!Number.isFinite(offset) || offset <= 0 || offset >= 1) throw new Error(`${rule.purpose === "stop_loss" ? "停損" : "停利"}百分比必須大於 0 且小於 100%。`);
    }
    if ((rule.purpose === "stop_loss" || rule.purpose === "take_profit") && style !== "fixed_percent") {
      const target = Number(parameters.target_price ?? rule.expression.value);
      if (!Number.isFinite(target) || target <= 0) throw new Error(`${rule.purpose === "stop_loss" ? "停損" : "停利"}價格必須大於 0。`);
    }
    if (rule.purpose === "break_even") {
      const activation = Number(parameters.activation_profit_pct);
      const fees = ["entry_fee_rate", "exit_fee_rate", "slippage_rate"].map((key) => Number(parameters[key]));
      const lock = Number(parameters.lock_in_pct);
      if (!Number.isFinite(activation) || activation <= 0 || activation > 1) throw new Error("保本啟動距離必須大於 0 且不超過 100%。");
      if (fees.some((value) => !Number.isFinite(value) || value < 0 || value > .02)) throw new Error("保本手續費與滑價必須介於 0% 到 2%。");
      if (!Number.isFinite(lock) || lock < 0 || lock > .05) throw new Error("保本額外鎖利必須介於 0% 到 5%。");
    }
    if (rule.purpose === "trailing") {
      const activation = Number(parameters.activation_profit_pct);
      const distance = Number(parameters.distance_pct);
      const staleAfter = Number(parameters.stale_after_seconds);
      if (!Number.isFinite(activation) || activation <= 0 || activation > 1) throw new Error("移動規則啟動距離必須大於 0 且不超過 100%。");
      if (!Number.isFinite(distance) || distance <= 0 || distance > .5) throw new Error("移動規則追蹤距離必須大於 0 且不超過 50%。");
      if (!String(parameters.timeframe ?? "")) throw new Error("移動規則必須指定時間週期。");
      if (!Number.isInteger(staleAfter) || staleAfter < 5 || staleAfter > 3600) throw new Error("移動規則資料逾時必須介於 5 到 3600 秒。");
    }
    if (rule.purpose === "take_profit" && action.type === "reduce_position" && action.quantity_basis === "initial") {
      const fraction = Number(action.quantity_fraction);
      if (!Number.isFinite(fraction) || fraction <= 0 || fraction >= 1) throw new Error("分段停利比例必須大於 0% 且小於 100%。");
      initialReduction += fraction;
    }
  }
  if (initialReduction > 1 + 1e-12) throw new Error("所有依初始部位計算的分段停利比例總和不得超過 100%。");
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function rulesFrom(strategy: StrategySummary): CloseRule[] {
  const raw = object(strategy.default_rules).close_conditions;
  return Array.isArray(raw) ? raw.map((item) => {
    const rule = object(item);
    return {
      purpose: String(rule.purpose ?? "exit"),
      enabled: rule.enabled !== false,
      expression: object(rule.expression),
      metadata: object(rule.metadata),
    };
  }) : [];
}

function draftFrom(strategy: StrategySummary): Draft {
  const metadata = object(strategy.metadata);
  const displayQuantities = object(metadata.order_display_quantities);
  const referencePrices = object(metadata.sizing_reference_prices);
  return {
    name: strategy.name,
    instruments: strategy.target_instruments ?? [],
    side: metadata.position_side === "short" ? "short" : "long",
    displayQuantities: Object.fromEntries((strategy.target_instruments ?? []).map((instrument) => [instrument, String(displayQuantities[instrument] ?? "")])),
    referencePrices: Object.fromEntries((strategy.target_instruments ?? []).map((instrument) => [instrument, String(referencePrices[instrument] ?? "")])),
    slippagePercent: String(Number(metadata.max_entry_slippage_pct ?? .005) * 100),
    executionDelaySeconds: String(strategy.execution_delay_seconds ?? 0),
    entrySignal: object(strategy.entry_signal),
    closeRules: rulesFrom(strategy),
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = object(error.info).detail;
    if (typeof detail === "string") return detail;
    if (error.status === 409 && object(detail).current_updated_at) return `後端策略已在 ${new Date(String(object(detail).current_updated_at)).toLocaleString("zh-TW")} 更新。草稿尚未送出；請重新整理並核對後再儲存。`;
    const message = object(detail).message;
    if (typeof message === "string") return message;
  }
  return error instanceof Error ? error.message : "操作失敗，請檢查後端狀態。";
}

function SizeQuotePreview({ instrument, quantity, price, side }: { instrument: string; quantity: string; price: string; side: "long" | "short" }) {
  const quote = useSWR(
    quantity && price ? ["size-quote", instrument, quantity, price, side] : null,
    () => quoteInstrumentSize(instrument, { display_quantity: quantity, entry_price: price, side, rule_price: null }),
  );
  if (!quantity) return <small className="size-quote pending">輸入想開的幣量後，系統會用目前市價換算 OKX 合約口數。</small>;
  if (!price) return <small className="size-quote blocked">缺少目前市價，暫時不能安全換算或儲存。</small>;
  if (quote.error) return <small className="size-quote blocked">無法安全換算；請確認幣量、市價與 OKX 最小口數。</small>;
  if (!quote.data) return <small className="size-quote pending">正在換算...</small>;
  return <small className="size-quote ready">送給 OKX 的數量：{quote.data.api_quantity_contracts} 口 · 以此市價估約 {quote.data.estimated_notional_usdt} USDT</small>;
}

// Mirrors materialize_position_rule's fixed_percent math (src/trading/position_rule_model.py)
// so the sizing preview resolves the same stop price the backend will materialize at fill time.
// For fixed_price / evidence_target / previous_swing_level styles the price is already concrete.
function resolvedRulePrice(rule: CloseRule | undefined, side: "long" | "short", referencePrice: string): string {
  if (!rule) return "";
  const style = ruleStyle(rule);
  const parameters = ruleParameters(rule);
  if (style === "fixed_percent") {
    const ref = Number(referencePrice);
    const offset = Number(parameters.offset_pct ?? 0);
    if (!(ref > 0) || !(offset > 0)) return "";
    const favorable = rule.purpose === "take_profit";
    const increase = (side === "long") === favorable;
    return String(ref * (increase ? 1 + offset : 1 - offset));
  }
  const target = parameters.target_price ?? rule.expression.value;
  return target !== null && target !== undefined && target !== "" ? String(target) : "";
}

function SizeEntryCard({
  instrument,
  side,
  price,
  stopPrice,
  quantity,
  onQuantityChange,
  equityUsd,
  exchangeConnected,
  onRiskCandidateChange,
}: {
  instrument: string;
  side: "long" | "short";
  price: string;
  stopPrice: string;
  quantity: string;
  onQuantityChange: (value: string) => void;
  equityUsd: number | null;
  exchangeConnected: boolean;
  onRiskCandidateChange: (instrument: string, priceLossUsdt: number | null) => void;
}) {
  const [mode, setMode] = useState<"fixed" | "risk">("fixed");
  const [lossMode, setLossMode] = useState<"pct" | "usdt">("pct");
  const [lossPct, setLossPct] = useState("1");
  const [lossUsdt, setLossUsdt] = useState("");
  const canUsePct = exchangeConnected && equityUsd != null;
  const allowedLossUsdt = lossMode === "pct" && canUsePct ? String((Number(lossPct) / 100) * (equityUsd ?? 0)) : lossUsdt;
  const riskQuote = useSWR(
    mode === "risk" && instrument && price && stopPrice && Number(allowedLossUsdt) > 0
      ? ["strategy-risk-quote", instrument, price, stopPrice, allowedLossUsdt, side]
      : null,
    () => quoteInstrumentRisk(instrument, {
      mode: "chart_anchored",
      entry_price: price,
      side,
      allowed_loss_usdt: allowedLossUsdt,
      stop_price: stopPrice,
    }),
  );
  useEffect(() => {
    if (mode !== "risk") { onRiskCandidateChange(instrument, null); return; }
    if (riskQuote.data) {
      onQuantityChange(riskQuote.data.display_quantity);
      onRiskCandidateChange(instrument, Number(riskQuote.data.price_loss_usdt));
    } else {
      onRiskCandidateChange(instrument, null);
    }
    // Only the resolved quote (or leaving risk mode) should re-sync the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, riskQuote.data, instrument]);
  useEffect(
    () => () => onRiskCandidateChange(instrument, null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [instrument],
  );

  return (
    <div className="size-entry-card">
      <strong>{instrument}</strong>
      <div className="size-mode-toggle">
        <button type="button" className={mode === "fixed" ? "selected" : ""} onClick={() => setMode("fixed")}>固定幣量</button>
        <button type="button" className={mode === "risk" ? "selected" : ""} onClick={() => setMode("risk")}>依停損反推</button>
      </div>
      {mode === "fixed" ? (
        <label><small>想開幣量（{instrument.split("-")[0]}）</small><input type="number" min="0" step="any" value={quantity} onChange={(event) => onQuantityChange(event.target.value)} /></label>
      ) : (
        <>
          {!stopPrice && <div className="error-state">此策略尚未設定有效停損價，無法反推幣量。請先設定停損規則。</div>}
          <div className="size-mode-toggle">
            <button type="button" className={lossMode === "pct" ? "selected" : ""} disabled={!canUsePct} onClick={() => setLossMode("pct")}>% 資金</button>
            <button type="button" className={lossMode === "usdt" ? "selected" : ""} onClick={() => setLossMode("usdt")}>USDT</button>
          </div>
          {!canUsePct && <small>{exchangeConnected ? "尚無帳戶權益資料。" : "Simulation 無交易所連線，無法取得權益。"}請改用 USDT 金額。</small>}
          {lossMode === "pct" ? (
            <label><small>願意輸的資金（% 總權益）</small><span className="input-with-suffix"><input type="number" min="0" step="0.01" value={lossPct} onChange={(event) => setLossPct(event.target.value)} disabled={!canUsePct} /><small>%</small></span>{canUsePct && Number(lossPct) > 0 && <small className="conversion-hint">≈ {((Number(lossPct) / 100) * (equityUsd ?? 0)).toFixed(2)} USDT</small>}</label>
          ) : (
            <label><small>願意輸的資金（USDT）</small><input type="number" min="0" step="any" value={lossUsdt} onChange={(event) => setLossUsdt(event.target.value)} /></label>
          )}
          {riskQuote.error && <div className="error-state">目前風險預算與停損距離無法安全換算成合法 lot size 部位。</div>}
          {riskQuote.data && <div className="risk-sizing-result">
            <div><small>反推幣量</small><strong>{riskQuote.data.display_quantity} {riskQuote.data.display_currency}</strong></div>
            <div><small>停損距離</small><strong>{(Number(riskQuote.data.stop_distance_pct) * 100).toFixed(2)}%</strong></div>
          </div>}
        </>
      )}
      <div className="size-reference-price"><small>估算市價（自動）</small><strong>{price ? `${price} USDT` : "等待市場價格"}</strong></div>
      <SizeQuotePreview instrument={instrument} quantity={quantity} price={price} side={side} />
    </div>
  );
}

// Read-only preview only: the real, executable stop_loss/take_profit price for a
// previous_swing_level rule is resolved fresh at each future entry by the backend
// (resolve_dynamic_rule_templates), not frozen to whatever this preview showed.
function SwingLevelPreview({
  instrument,
  bar,
  kind,
  nth,
  minScore,
  bufferPct,
  onResolved,
}: {
  instrument: string;
  bar: string;
  kind: "support" | "resistance";
  nth: number;
  minScore: number;
  bufferPct: number;
  onResolved: (price: number) => void;
}) {
  const quote = useSWR(
    instrument && bar && nth > 0
      ? ["swing-stop-quote", instrument, bar, kind, nth, minScore, bufferPct]
      : null,
    () => quoteSwingStopLevel(instrument, { bar, kind, nth, min_score: minScore, buffer_pct: bufferPct }),
  );
  useEffect(() => {
    if (quote.data) onResolved(quote.data.price);
    // Only a freshly resolved quote should push a price update upstream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quote.data]);
  if (!instrument) return <small className="size-quote pending">請先選擇交易商品以預覽前高/前低價位。</small>;
  if (quote.error) return <small className="size-quote blocked">{errorMessage(quote.error)}</small>;
  if (!quote.data) return <small className="size-quote pending">正在計算前高/前低…</small>;
  const evidence = object(quote.data.evidence);
  return <small className="size-quote ready">
    以 {instrument} 預覽：解析價位 {quote.data.price.toFixed(4)}（原始 {quote.data.raw_pivot_price.toFixed(4)}，
    第 {String(evidence.nth)} 個{kind === "support" ? "前低" : "前高"}，score {Number(evidence.score ?? 0).toFixed(2)}，
    觸及 {String(evidence.touches)} 次，最近觸及 {evidence.latest_touch_at ? new Date(String(evidence.latest_touch_at)).toLocaleString("zh-TW") : "-"}）
  </small>;
}

function TypedCloseRuleEditor({ rule, side, onChange, analysisSymbols = [] }: { rule: CloseRule; side: "long" | "short"; onChange: (rule: CloseRule) => void; analysisSymbols?: string[] }) {
  const style = ruleStyle(rule);
  const parameters = ruleParameters(rule);
  const definition = ruleDefinition(rule);
  const action = object(definition.action);
  const setParameters = (next: Record<string, unknown>, nextAction?: Record<string, unknown>) => onChange(withTypedRule(rule, style, next, nextAction ?? action));
  const defaultSwingKind = (rule.purpose === "stop_loss") === (side === "long") ? "support" : "resistance";
  const setStyle = (nextStyle: string) => {
    if (nextStyle === "fixed_percent") onChange(withTypedRule({ ...rule, expression: priceExpression(rule.purpose, side, 1) }, nextStyle, { offset_pct: String(parameters.offset_pct ?? (rule.purpose === "stop_loss" ? "0.01" : "0.02")) }, action));
    else if (nextStyle === "previous_swing_level") onChange(withTypedRule({ ...rule, expression: priceExpression(rule.purpose, side, 1) }, nextStyle, {
      bar: String(parameters.bar ?? "1H"),
      kind: String(parameters.kind ?? defaultSwingKind),
      nth: String(parameters.nth ?? "1"),
      min_score: String(parameters.min_score ?? "0.5"),
      buffer_pct: String(parameters.buffer_pct ?? "0.002"),
    }, action));
    else onChange(withTypedRule(rule, nextStyle, { target_price: String(parameters.target_price ?? rule.expression.value ?? "") }, action));
  };
  if (rule.purpose === "stop_loss" || rule.purpose === "take_profit") {
    const staged = rule.purpose === "take_profit" && String(action.type ?? "close_position") === "reduce_position";
    const price = String(parameters.target_price ?? rule.expression.value ?? "");
    const swingKind = (parameters.kind === "resistance" ? "resistance" : "support") as "support" | "resistance";
    const swingNth = Math.max(1, Math.round(Number(parameters.nth ?? 1)) || 1);
    const swingMinScore = Number(parameters.min_score ?? 0.5);
    const swingBufferPct = Number(parameters.buffer_pct ?? 0.002);
    return <div className="typed-rule-grid">
      <label className="field"><span>計算方式</span><select value={style === "fixed_percent" || style === "previous_swing_level" ? style : "fixed_price"} onChange={(event) => setStyle(event.target.value)}><option value="fixed_percent">依進場價百分比</option><option value="fixed_price">固定價格</option><option value="previous_swing_level">前高/前低（自動）</option></select></label>
      {style === "fixed_percent" ? <label className="field"><span>{rule.purpose === "stop_loss" ? "停損距離" : "獲利目標"}</span><span className="input-with-suffix"><input type="number" min="0.0001" max="100" step="0.01" value={String(Number(parameters.offset_pct ?? 0) * 100)} onChange={(event) => setParameters({ ...parameters, offset_pct: String(Number(event.target.value) / 100) })} /><small>%</small></span></label>
      : style === "previous_swing_level" ? <>
        <label className="field"><span>時間框架</span><select value={String(parameters.bar ?? "1H")} onChange={(event) => setParameters({ ...parameters, bar: event.target.value })}><option value="5m">5m</option><option value="15m">15m</option><option value="1H">1H</option><option value="4H">4H</option><option value="1D">1D</option></select></label>
        <label className="field"><span>方向</span><select value={swingKind} onChange={(event) => setParameters({ ...parameters, kind: event.target.value })}><option value="support">前低（支撐）</option><option value="resistance">前高（壓力）</option></select></label>
        <label className="field"><span>第幾個前{swingKind === "support" ? "低" : "高"}</span><input type="number" min="1" max="50" step="1" value={String(swingNth)} onChange={(event) => setParameters({ ...parameters, nth: String(Math.max(1, Math.round(Number(event.target.value) || 1))) })} /></label>
        <label className="field"><span>最低信心分數</span><span className="input-with-suffix"><input type="number" min="0" max="100" step="1" value={String(Math.round(swingMinScore * 100))} onChange={(event) => setParameters({ ...parameters, min_score: String(Math.min(1, Math.max(0, Number(event.target.value) / 100))) })} /><small>%</small></span></label>
        <label className="field"><span>緩衝距離</span><span className="input-with-suffix"><input type="number" min="0" max="50" step="0.01" value={String(swingBufferPct * 100)} onChange={(event) => setParameters({ ...parameters, buffer_pct: String(Math.min(0.5, Math.max(0, Number(event.target.value) / 100))) })} /><small>%</small></span></label>
        {analysisSymbols[0] && <SwingLevelPreview instrument={analysisSymbols[0]} bar={String(parameters.bar ?? "1H")} kind={swingKind} nth={swingNth} minScore={swingMinScore} bufferPct={swingBufferPct} onResolved={(resolvedPrice) => onChange(withTypedRule({ ...rule, expression: priceExpression(rule.purpose, side, resolvedPrice) }, "previous_swing_level", parameters, action))} />}
        <div className="inline-warning">每次新進場都會用當下 K 線重新計算前高/前低；此處預覽僅供參考，實際套用時每個商品各自計算。</div>
      </>
      : <label className="field"><span>{rule.purpose === "stop_loss" ? "停損價" : "停利價"}</span><input type="number" min="0" step="any" value={price} onChange={(event) => { const value = event.target.value; onChange(withTypedRule({ ...rule, expression: priceExpression(rule.purpose, side, Number(value)) }, "fixed_price", { ...parameters, target_price: value }, action)); }} /></label>}
      {rule.purpose === "take_profit" && <label className="check-field"><input type="checkbox" checked={staged} onChange={(event) => onChange(withTypedRule(rule, style, parameters, event.target.checked ? { type: "reduce_position", quantity_fraction: 0.5, quantity_basis: "initial" } : { type: "close_position" }))} /> 分段減倉，保留剩餘部位續跑</label>}
      {staged && <><label className="field"><span>減倉比例</span><span className="input-with-suffix"><input type="number" min="0.01" max="99.99" step="1" value={String(Number(action.quantity_fraction ?? 0.5) * 100)} onChange={(event) => onChange(withTypedRule(rule, style, parameters, { ...action, quantity_fraction: Number(event.target.value) / 100, quantity_basis: String(action.quantity_basis ?? "initial") }))} /><small>%</small></span></label><label className="field"><span>比例基準</span><select value={String(action.quantity_basis ?? "initial")} onChange={(event) => onChange(withTypedRule(rule, style, parameters, { ...action, quantity_basis: event.target.value }))}><option value="initial">初始部位</option><option value="remaining">當時剩餘部位</option></select></label></>}
      <div className="inline-warning">相對價格會在確認成交均價後重新物化；固定價格仍會驗證多空方向。</div>
    </div>;
  }
  if (rule.purpose === "break_even") {
    const percentField = (key: string, label: string, max: number) => <label className="field"><span>{label}</span><span className="input-with-suffix"><input type="number" min="0" max={max} step="0.001" value={String(Number(parameters[key] ?? 0) * 100)} onChange={(event) => setParameters({ ...parameters, [key]: String(Number(event.target.value) / 100) }, { type: "amend_stop" })} /><small>%</small></span></label>;
    return <div className="typed-rule-grid">
      <label className="field"><span>啟動獲利距離</span><span className="input-with-suffix"><input type="number" min="0.0001" max="100" step="0.01" value={String(Number(parameters.activation_profit_pct ?? 0.01) * 100)} onChange={(event) => setParameters({ ...parameters, activation_profit_pct: String(Number(event.target.value) / 100) }, { type: "amend_stop" })} /><small>%</small></span></label>
      {percentField("entry_fee_rate", "進場手續費", 2)}
      {percentField("exit_fee_rate", "出場手續費", 2)}
      {percentField("slippage_rate", "每側滑價", 2)}
      {percentField("lock_in_pct", "額外鎖利", 5)}
      <div className="inline-warning">保本價不是原始進場價；後端會計入進出場手續費、雙側滑價與鎖利，並只在交易所確認修改停損後標記完成。</div>
    </div>;
  }
  if (rule.purpose === "trailing") {
    const kind = String(parameters.trailing_kind ?? "stop");
    const isReduce = kind === "take_profit" && action.type === "reduce_position";
    const changeKind = (nextKind: string) => onChange(withTypedRule(rule, "trailing_threshold", { ...parameters, trailing_kind: nextKind }, nextKind === "stop" ? { type: "amend_stop" } : { type: "close_position" }));
    return <div className="typed-rule-grid">
      <label className="field"><span>追蹤用途</span><select value={kind} onChange={(event) => changeKind(event.target.value)}><option value="stop">單調收緊保護停損</option><option value="take_profit">回撤後停利</option></select></label>
      <label className="field"><span>啟動獲利距離</span><span className="input-with-suffix"><input type="number" min="0.0001" max="100" step="0.01" value={String(Number(parameters.activation_profit_pct ?? .03) * 100)} onChange={(event) => setParameters({ ...parameters, activation_profit_pct: String(Number(event.target.value) / 100) })} /><small>%</small></span></label>
      <label className="field"><span>追蹤距離</span><span className="input-with-suffix"><input type="number" min="0.0001" max="50" step="0.01" value={String(Number(parameters.distance_pct ?? .02) * 100)} onChange={(event) => setParameters({ ...parameters, distance_pct: String(Number(event.target.value) / 100) })} /><small>%</small></span></label>
      <label className="field"><span>時間週期</span><select value={String(parameters.timeframe ?? "1m")} onChange={(event) => setParameters({ ...parameters, timeframe: event.target.value })}><option value="1m">1m</option><option value="5m">5m</option><option value="15m">15m</option><option value="1H">1H</option><option value="4H">4H</option></select></label>
      <label className="field"><span>資料逾時</span><span className="input-with-suffix"><input type="number" min="5" max="3600" step="1" value={String(parameters.stale_after_seconds ?? 90)} onChange={(event) => setParameters({ ...parameters, stale_after_seconds: Number(event.target.value) })} /><small>秒</small></span></label>
      {kind === "take_profit" && <label className="check-field"><input type="checkbox" checked={isReduce} onChange={(event) => onChange(withTypedRule(rule, "trailing_threshold", parameters, event.target.checked ? { type: "reduce_position", quantity_fraction: .5, quantity_basis: "remaining" } : { type: "close_position" }))} /> 只減倉，不全部平倉</label>}
      {isReduce && <label className="field"><span>減倉比例</span><span className="input-with-suffix"><input type="number" min="0.01" max="99.99" step="1" value={String(Number(action.quantity_fraction ?? .5) * 100)} onChange={(event) => onChange(withTypedRule(rule, "trailing_threshold", parameters, { ...action, quantity_fraction: Number(event.target.value) / 100, quantity_basis: "remaining" }))} /><small>%</small></span></label>}
      <div className="inline-warning">移動停損只會收緊既有保護；移動停利則等待水位回撤後才平倉或減倉。缺少新鮮價格時兩者都不會前進。</div>
    </div>;
  }
  return <ExpressionEditor value={rule.expression} onChange={(expression) => onChange({ ...rule, expression })} label={`${rule.purpose.replaceAll("_", " ")} 條件`} analysisSymbols={analysisSymbols} />;
}

function StrategyList({ strategies, selectedId, onSelect, onCreate }: {
  strategies: StrategySummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <MotionConfig reducedMotion="user">
    <aside className="strategy-list panel">
      <div className="panel-heading">
        <div><h2>策略</h2><p>{strategies.length} 個已儲存策略</p></div>
        <button className="icon-button" type="button" onClick={onCreate} aria-label="建立策略"><CirclePlus size={20} /></button>
      </div>
      <div className="strategy-list-items">
        {strategies.map((strategy) => (
          <button type="button" className={`strategy-list-item ${selectedId === strategy.id ? "selected" : ""}`} key={strategy.id} onClick={() => onSelect(strategy.id)}>
            {selectedId === strategy.id && <motion.span layoutId="strategy-list-active-rail" className="list-active-rail" transition={{ type: "spring", stiffness: 500, damping: 35 }} />}
            <span className={`status-dot ${strategy.enabled ? "on" : "off"}`} />
            <span className="strategy-list-copy"><strong>{strategy.name}</strong><small>{strategy.target_instruments?.join(" · ") || "尚未指定商品"}</small></span>
            <ChevronRight size={17} />
          </button>
        ))}
        {!strategies.length && <div className="empty-state">還沒有策略。建立第一個進場計畫。</div>}
      </div>
    </aside>
    </MotionConfig>
  );
}

function ChildSignal({ strategyId, strategyUpdatedAt, strategyEnabled, signal, onSaved, analysisSymbols = [] }: { strategyId: string; strategyUpdatedAt: string; strategyEnabled: boolean; signal?: PersistedSignalExpression; onSaved: () => Promise<unknown>; analysisSymbols?: string[] }) {
  const mutationRef = useRef(false);
  const [purpose, setPurpose] = useState(signal?.purpose ?? "filter");
  const [expression, setExpression] = useState<SignalExpression>(object(signal?.expression).type || object(signal?.expression).op ? object(signal?.expression) : { type: "price_above", symbol: "BTC-USDT-SWAP", value: 0 });
  const [dirty, setDirty] = useState(!signal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    if (mutationRef.current) return;
    mutationRef.current = true;
    setBusy(true); setError("");
    try {
      const validation = await validateSignal({ expression });
      if (!validation.valid) throw new Error(validation.errors?.join("；") || "規則格式不正確");
      if (strategyEnabled && signal && !confirm("此策略目前已啟用。確認先停用並進入審查，再儲存訊號變更？停用完成前仍會執行舊版本。")) return;
      if (signal) await updateStrategySignal(strategyId, signal.id, { expected_updated_at: signal.updated_at, purpose, expression: validation.normalized ?? expression, confirm_disable_for_review: strategyEnabled ? true : undefined });
      else {
        if (!strategyUpdatedAt) throw new Error("策略版本時間缺失，無法安全新增子訊號。請重新整理。");
        if (strategyEnabled && !confirm("此策略已啟用。新增子訊號會改變下一次進場／出場條件；確定新增至目前檢視的策略版本？")) return;
        await createStrategySignal(strategyId, { confirm: true, expected_strategy_updated_at: strategyUpdatedAt, purpose, expression: validation.normalized ?? expression, confirm_disable_for_review: strategyEnabled ? true : undefined });
      }
      setDirty(false); await onSaved();
    } catch (caught) { setError(errorMessage(caught)); } finally { mutationRef.current = false; setBusy(false); }
  };
  const remove = async () => {
    if (!signal || !confirm("確定刪除此附加訊號？若策略因此不完整，後端會自動停用策略。")) return;
    setBusy(true); setError("");
    try { await deleteStrategySignal(strategyId, signal.id, signal.updated_at, strategyEnabled); await onSaved(); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  };
  return (
    <motion.div className="sub-editor" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}>
      <div className="sub-editor-head">
        <label className="field"><span>用途</span><select value={purpose} onChange={(event) => { setPurpose(event.target.value); setDirty(true); }}><option value="entry">進場條件</option><option value="filter">進場過濾</option><option value="exit">複製至部位的出場條件</option></select></label>
        <span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "尚未儲存" : "已儲存"}</span>
      </div>
      <ExpressionEditor value={expression} onChange={(next) => { setExpression(next); setDirty(true); }} label="附加訊號" analysisSymbols={analysisSymbols} />
      {error && <div className="error-state">{error}</div>}
      <div className="form-actions">
        {signal && <button type="button" className="btn btn-danger" disabled={busy} onClick={remove}><Trash2 size={15} /> 刪除</button>}
        <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}><Save size={15} /> {busy ? "儲存中…" : "儲存訊號"}</button>
      </div>
    </motion.div>
  );
}

function StrategyEditor({ strategy, onSaved, catalog, catalogStale, allowedInstruments }: { strategy?: StrategySummary; onSaved: (selectedId?: string) => Promise<unknown>; catalog: InstrumentMetadataResponse[]; catalogStale: boolean; allowedInstruments?: string[] }) {
  const mutationRef = useRef(false);
  const [draft, setDraft] = useState<Draft>(() => strategy ? draftFrom(strategy) : blankDraft());
  const [dirty, setDirty] = useState(!strategy);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newSignal, setNewSignal] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const marketOverview = useSWR("strategy-sizing-market-overview", () => getMarketOverview({ instType: "SWAP" }), { refreshInterval: 15_000 });
  const preflight = useSWR("strategy-editor-preflight", getLivePreflight);
  const exchangeConnected = preflight.data?.execution_mode === "demo" || preflight.data?.execution_mode === "live_armed" || preflight.data?.execution_mode === "live_safe";
  const envelopeUsage = useSWR(exchangeConnected ? "risk-envelope-usage" : null, getRiskEnvelopeUsage, { refreshInterval: 15_000 });
  const [riskCandidates, setRiskCandidates] = useState<Record<string, number | null>>({});
  const setRiskCandidate = (instrument: string, priceLossUsdt: number | null) => setRiskCandidates((current) => (current[instrument] === priceLossUsdt ? current : { ...current, [instrument]: priceLossUsdt }));
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => { setDraft((current) => ({ ...current, [key]: value })); setDirty(true); setError(""); };
  const instruments = draft.instruments;
  const livePrices = Object.fromEntries((marketOverview.data?.items ?? []).map((item) => [item.inst_id, item.last_price]));
  const sizingPrice = (instrument: string): string => livePrices[instrument] || draft.referencePrices[instrument] || "";
  const stopLossRule = draft.closeRules.find((rule) => rule.purpose === "stop_loss" && rule.enabled);
  const outsideAllowlist = allowedInstruments
    ? instruments.filter((item) => !allowedInstruments.includes(item))
    : [];
  const candidateWorstCaseLossUsd = instruments.reduce((total, instrument) => total + (riskCandidates[instrument] ?? 0), 0);
  const projectedWorstCaseLossUsd = (envelopeUsage.data?.existing_worst_case_loss_usd ?? 0) + candidateWorstCaseLossUsd;
  const overEnvelopeBudget = Boolean(envelopeUsage.data) && candidateWorstCaseLossUsd > 0 && projectedWorstCaseLossUsd > (envelopeUsage.data?.loss_budget_usd ?? Infinity);

  const save = async () => {
    if (mutationRef.current) return;
    mutationRef.current = true;
    setBusy(true); setError("");
    try {
      if (!draft.name.trim()) throw new Error("請輸入策略名稱。");
      if (!instruments.length) throw new Error("至少需要一個交易商品。");
      if (strategy && !strategy.updated_at) throw new Error("策略版本時間缺失，無法安全儲存。請重新整理。");
      if (outsideAllowlist.length) throw new Error(`商品超出帳戶風險 allowlist：${outsideAllowlist.join("、")}`);
      if (overEnvelopeBudget) throw new Error(`此策略的停損風險（預估 ${projectedWorstCaseLossUsd.toFixed(2)} USDT）加上既有部位後，會超出風險信封預算（${(envelopeUsage.data?.loss_budget_usd ?? 0).toFixed(2)} USDT）。請調高停損距離、降低反推的資金比例，或先至風險上限頁調整。`);
      const maxEntrySlippagePct = Number(draft.slippagePercent) / 100;
      if (!Number.isFinite(maxEntrySlippagePct) || maxEntrySlippagePct <= 0 || maxEntrySlippagePct > 0.05) throw new Error("metadata.max_entry_slippage_pct must be greater than 0 and at most 0.05");
      const executionDelaySeconds = Number(draft.executionDelaySeconds);
      if (!Number.isInteger(executionDelaySeconds) || executionDelaySeconds < 0 || executionDelaySeconds > 86400) throw new Error("執行延遲必須是 0 到 86400 秒的整數。");
      validateTypedCloseRules(draft.closeRules);
      const expressions = [draft.entrySignal, ...draft.closeRules.map((rule) => rule.expression)];
      const validated = await Promise.all(expressions.map((expression) => validateSignal({ expression })));
      const invalid = validated.find((result) => !result.valid);
      if (invalid) throw new Error(invalid.errors?.join("；") || "規則格式不正確");
      const referencePrices = Object.fromEntries(instruments.map((instrument) => [instrument, sizingPrice(instrument)]));
      const missingPrices = instruments.filter((instrument) => !referencePrices[instrument]);
      if (missingPrices.length) throw new Error(`缺少目前市價，無法換算 OKX 口數：${missingPrices.join("、")}。請確認市場總覽 API 可用後再儲存。`);
      const missingQuantities = instruments.filter((instrument) => !(Number(draft.displayQuantities[instrument]) > 0));
      if (missingQuantities.length) throw new Error(`請先輸入想開幣量：${missingQuantities.join("、")}。`);
      let quotes;
      try {
        quotes = await Promise.all(instruments.map((instrument) => quoteInstrumentSize(instrument, {
          display_quantity: draft.displayQuantities[instrument] ?? "",
          entry_price: referencePrices[instrument],
          side: draft.side,
          rule_price: null,
        })));
      } catch (caught) {
        if (caught instanceof ApiError && typeof object(caught.info).detail !== "string" && typeof object(object(caught.info).detail).message !== "string") {
          throw new Error("想開幣量無法安全換算成 OKX 口數；請確認幣量是否符合該商品的最小下單量與精度。");
        }
        throw caught;
      }
      const sizes = Object.fromEntries(quotes.map((quote) => [quote.inst_id, quote.api_quantity_contracts]));
      const payload = {
        name: draft.name.trim(),
        kind: "signal",
        target_instruments: instruments,
        entry_signal: validated[0].normalized ?? draft.entrySignal,
        default_rules: { close_conditions: draft.closeRules.map((rule, index) => ({ ...rule, expression: validated[index + 1].normalized ?? rule.expression })) },
        execution_delay_seconds: executionDelaySeconds,
        metadata: { ...object(strategy?.metadata), position_side: draft.side, order_size_contracts: sizes, order_display_quantities: draft.displayQuantities, sizing_reference_prices: referencePrices, max_entry_slippage_pct: String(maxEntrySlippagePct) },
      };
      if (strategy?.enabled && !confirm("此策略目前已啟用。確認先停用並進入審查，再儲存完整策略變更？停用完成前仍會執行舊版本。")) return;
      const saved = strategy
        ? await updateStrategy(strategy.id, { ...payload, expected_updated_at: strategy.updated_at!, confirm_disable_for_review: strategy.enabled ? true : undefined })
        : await createStrategy({ ...payload, enabled: false });
      setDirty(false); await onSaved(saved.id);
    } catch (caught) { setError(errorMessage(caught)); } finally { mutationRef.current = false; setBusy(false); }
  };

  const toggle = async () => {
    if (!strategy) return;
    if (!strategy.enabled && !confirm("啟用策略後，在實盤已武裝且進場閘門開啟時可能建立真實部位。確定啟用？")) return;
    setBusy(true); setError("");
    try {
      if (strategy.enabled) await disableStrategy(strategy.id);
      else {
        if (!strategy.updated_at) throw new Error("策略版本時間缺失，無法安全確認啟用。請重新整理。");
        await enableStrategy(strategy.id, strategy.updated_at);
      }
      await onSaved(strategy.id);
    }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!strategy) return;
    setBusy(true); setError("");
    try {
      if (!strategy.updated_at) throw new Error("策略缺少版本時間，無法安全確認刪除；請重新整理後再試。");
      await deleteStrategy(strategy.id, strategy.updated_at); await onSaved();
    }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  };
  const updateRule = (index: number, next: CloseRule) => { const rules = [...draft.closeRules]; rules[index] = next; set("closeRules", rules); };
  return (
    <section className="strategy-editor panel">
      <div className="panel-heading">
        <div><h2>{strategy ? strategy.name : "建立新策略"}</h2><p>{strategy ? <span className="mono">{strategy.id}</span> : "新策略一律以停用狀態建立"}</p></div>
        {strategy && <div className="status-row"><span className={`badge ${strategy.enabled ? "success" : "warning"}`}>{strategy.enabled ? "已啟用" : "已停用"}</span><span className={`badge ${strategy.readiness === "ready" ? "success" : "warning"}`}>{strategy.readiness ?? "unknown"}</span>{Boolean(strategy.runtime?.pending_executions?.length) && <span className="badge warning">等待執行 {strategy.runtime?.pending_executions?.length ?? 0}</span>}</div>}
      </div>
      <div className="form-grid">
        <label className="field"><span>策略名稱</span><input value={draft.name} onChange={(event) => set("name", event.target.value)} /></label>
        <label className="field"><span>方向</span><select value={draft.side} onChange={(event) => set("side", event.target.value as "long" | "short")}><option value="long">做多 Long</option><option value="short">做空 Short</option></select></label>
        <div className="field full"><span>交易商品</span><InstrumentSelector instruments={catalog} stale={catalogStale} multiple value={draft.instruments} onChange={(value) => set("instruments", value)} eligibleInstruments={allowedInstruments} />{allowedInstruments ? <small>顯示所有可交易 SWAP；選項會標示是否位於帳戶 allowlist（目前允許 {allowedInstruments.length} 個）。</small> : <div className="inline-warning">帳戶風險 allowlist 尚未建立；策略可先儲存為停用，但實盤不能通過 preflight。</div>}<AnimatePresence>{outsideAllowlist.length > 0 && <motion.div key="outside-allowlist" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}>目前策略超出帳戶 allowlist：{outsideAllowlist.join("、")}。請移除商品或先至「風險上限」調整邊界。</motion.div>}</AnimatePresence></div>
        <label className="field"><span>最大進場滑價</span><span className="input-with-suffix"><input type="number" min="0.0001" max="5" step="0.1" value={draft.slippagePercent} onChange={(event) => set("slippagePercent", event.target.value)} /><small>%</small></span></label>
        <label className="field"><span>訊號成立後執行延遲</span><span className="input-with-suffix"><input type="number" min="0" max="86400" step="1" value={draft.executionDelaySeconds} onChange={(event) => set("executionDelaySeconds", event.target.value)} /><small>秒</small></span><small>{Number(draft.executionDelaySeconds) > 0 ? "到期後會重新驗證訊號與風險；失效即取消。" : "0 秒＝停用延遲，沿用立即執行。"}</small></label>
        <div className="field full">
          <span>每個商品想開的幣量</span>
          <small>固定幣量：直接輸入幣量，系統用目前市價換算 OKX 合約口數。依停損反推：輸入願意在停損時損失的資金（% 權益或 USDT），系統依下方停損距離反推幣量。策略觸發時仍用當下行情與滑價設定送單。</small>
          <div className="contract-size-grid">{instruments.map((instrument) => {
            const price = sizingPrice(instrument);
            const stopPrice = resolvedRulePrice(stopLossRule, draft.side, price);
            return <SizeEntryCard
              key={instrument}
              instrument={instrument}
              side={draft.side}
              price={price}
              stopPrice={stopPrice}
              quantity={draft.displayQuantities[instrument] ?? ""}
              onQuantityChange={(value) => set("displayQuantities", { ...draft.displayQuantities, [instrument]: value })}
              equityUsd={envelopeUsage.data?.equity_usd ?? null}
              exchangeConnected={exchangeConnected}
              onRiskCandidateChange={setRiskCandidate}
            />;
          })}</div>
          {!exchangeConnected && <small>Simulation 無交易所連線，無法即時檢查風險信封使用量；「依停損反推」仍可用 USDT 金額，但不會顯示信封警告。</small>}
          <AnimatePresence>{overEnvelopeBudget && <motion.div key="envelope-over-budget" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}><AlertTriangle size={15} /> 此策略的停損風險（預估 {projectedWorstCaseLossUsd.toFixed(2)} USDT，含既有部位 {(envelopeUsage.data?.existing_worst_case_loss_usd ?? 0).toFixed(2)} USDT）會超出風險信封預算（{(envelopeUsage.data?.loss_budget_usd ?? 0).toFixed(2)} USDT）。無法儲存，請調整停損距離或降低反推的資金比例。</motion.div>}</AnimatePresence>
        </div>
        <div className="full"><ExpressionEditor value={draft.entrySignal} onChange={(value) => set("entrySignal", value)} label="主要進場訊號" analysisSymbols={draft.instruments} /></div>
      </div>

      <AnimatePresence>
        {strategy?.runtime?.pending_executions?.length ? <motion.div key="pending-executions" className="pending-execution-list" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}>{strategy.runtime.pending_executions.map((pending) => <article key={String(pending.correlation_id)}><span className="badge warning">等待重新驗證</span><strong>{String(pending.inst_id)}</strong><small>預定：{new Date(String(pending.due_at)).toLocaleString("zh-TW")}</small><small className="mono">{String(pending.correlation_id)}</small></article>)}</motion.div> : null}
      </AnimatePresence>

      <div className="section-divider"><div><h3>預設部位規則</h3><p>每次進場都會複製一份至新的 Maybech 邏輯部位；停損、停利與保本使用後端可物化的型別化參數。</p></div><button type="button" className="btn btn-outline" onClick={() => set("closeRules", [...draft.closeRules, defaultCloseRule("break_even", draft.side)])}><CirclePlus size={15} /> 新增保本規則</button></div>
      <div className="rule-stack">
        {draft.closeRules.map((rule, index) => (
          // key={index} is unstable across removals (draft rules have no backend id yet);
          // wrapping this in AnimatePresence would let it defer/misattribute exit animations
          // across shifted indices, so this only gets a mount-in fade (no exit animation).
          <motion.div className="sub-editor" key={index} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .2, ease: "easeOut" }}>
            <div className="sub-editor-head">
              <label className="field"><span>規則類型</span><select value={rule.purpose} onChange={(event) => updateRule(index, defaultCloseRule(event.target.value, draft.side))}><option value="stop_loss">停損</option><option value="take_profit">停利／分段停利</option><option value="break_even">自動保本</option><option value="trailing">移動停損／移動停利</option><option value="manual_review">人工檢查</option><option value="exit">一般出場</option></select></label>
              <label className="check-field"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(index, { ...rule, enabled: event.target.checked })} /> 啟用</label>
              <button type="button" className="icon-button danger-ghost" aria-label="移除規則" onClick={() => set("closeRules", draft.closeRules.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={16} /></button>
            </div>
            <TypedCloseRuleEditor rule={rule} side={draft.side} onChange={(next) => updateRule(index, next)} analysisSymbols={draft.instruments} />
          </motion.div>
        ))}
      </div>
      <AnimatePresence>
        {error && <motion.div key="strategy-editor-error" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}><AlertTriangle size={16} /> {error}</motion.div>}
      </AnimatePresence>
      <div className="form-actions">
        <span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "有尚未儲存的變更" : <><Check size={15} /> 已儲存</>}</span>
        {strategy && <button type="button" className={strategy.enabled ? "btn btn-outline" : "btn btn-danger"} disabled={busy || dirty || (!strategy.enabled && outsideAllowlist.length > 0)} onClick={toggle}><Power size={15} /> {strategy.enabled ? "停用策略" : "啟用策略"}</button>}
        <button type="button" className="btn btn-primary" disabled={busy || !dirty || overEnvelopeBudget} onClick={save}><Save size={16} /> {busy ? "儲存中…" : "儲存策略"}</button>
      </div>

      {strategy && <>
        <div className="section-divider"><div><h3>附加訊號</h3><p>Entry／Filter 會與主要訊號以 AND 組合；Exit 會複製至新部位。</p></div><button type="button" className="btn btn-outline" onClick={() => setNewSignal(true)}><CirclePlus size={15} /> 新增訊號</button></div>
        <div className="rule-stack">
          <AnimatePresence>
            {strategy.signal_expressions?.map((signal) => <ChildSignal key={signal.id} strategyId={strategy.id} strategyUpdatedAt={strategy.updated_at ?? ""} strategyEnabled={strategy.enabled} signal={signal} onSaved={() => onSaved(strategy.id)} analysisSymbols={draft.instruments} />)}
            {newSignal && <ChildSignal key="new-signal" strategyId={strategy.id} strategyUpdatedAt={strategy.updated_at ?? ""} strategyEnabled={strategy.enabled} onSaved={async () => { setNewSignal(false); return onSaved(strategy.id); }} analysisSymbols={draft.instruments} />}
          </AnimatePresence>
          {!strategy.signal_expressions?.length && !newSignal && <div className="empty-state">沒有附加訊號；主要進場訊號仍會獨立運作。</div>}
        </div>
        <div className="danger-zone strategy-delete">
          <div><strong>永久刪除策略</strong><p>只有已停用且沒有任何邏輯部位或舊交易歷史的策略才能刪除。</p></div>
          {confirmingDelete ? (
            <div className="delete-confirm">
              <p>確認永久刪除已停用的策略「{strategy.name}」？已有部位歷史時後端會拒絕，此動作無法復原。</p>
              <div className="form-actions">
                <button type="button" className="btn btn-outline" disabled={busy} onClick={() => setConfirmingDelete(false)}>取消</button>
                <button type="button" className="btn btn-danger" disabled={busy} onClick={remove}><Trash2 size={15} /> {busy ? "刪除中…" : "確認永久刪除"}</button>
              </div>
            </div>
          ) : (
            <button type="button" className="btn btn-danger" disabled={busy || strategy.enabled} onClick={() => setConfirmingDelete(true)}><Trash2 size={15} /> 刪除策略</button>
          )}
        </div>
      </>}
    </section>
  );
}

function Decisions({ strategyId }: { strategyId: string }) {
  const { data, error } = useSWR(["strategy-decisions", strategyId], () => listPersistedStrategyDecisions(strategyId, { limit: 20 }), { refreshInterval: 5000 });
  return <details className="panel audit-details"><summary className="audit-summary"><h2>最近決策與證據</h2><span className="badge info">{data?.length ?? 0} 筆</span></summary><p className="muted">重新啟動後仍保留的 SQLite 稽核紀錄</p>{error ? <div className="error-state">無法讀取策略決策。</div> : !data ? <div className="loading-state">讀取中…</div> : data.length ? <div className="decision-grid">{data.map((decision, index) => <article key={decision.id ?? index} className="decision-card"><div className="status-row"><span className={`badge ${decision.allowed ? "success" : "danger"}`}>{decision.allowed ? "允許" : "封鎖"}</span><span className="badge info">{decision.execution_status ?? "未執行"}</span></div><strong>{decision.pair ?? "未知商品"}</strong><p>{decision.reason || "沒有提供原因"}</p><small>{decision.time ? new Date(decision.time).toLocaleString("zh-TW") : "時間未知"}</small></article>)}</div> : <div className="empty-state">尚無策略決策紀錄。</div>}</details>;
}

export default function StrategiesPage() {
  const { data: strategies, error, mutate, isLoading } = useSWR("strategies", listStrategies, { refreshInterval: 10_000 });
  const catalog = useSWR("instrument-metadata", listInstruments);
  const preflight = useSWR("runtime-preflight", getLivePreflight);
  const risk = useSWR("risk-limits", getRiskLimits, { refreshInterval: 10_000 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [catalogActionError, setCatalogActionError] = useState("");
  const selected = strategies?.find((strategy) => strategy.id === selectedId) ?? strategies?.[0];
  const refresh = async (id?: string) => { await mutate(); if (id) setSelectedId(id); else if (id === undefined) setSelectedId(null); setCreating(false); };
  const refreshCatalog = async () => {
    setCatalogActionError("");
    try {
      if (preflight.data?.execution_mode === "simulation") {
        await bootstrapSimulationInstruments();
      } else {
        await refreshInstruments();
      }
      await catalog.mutate();
    } catch (reason) {
      setCatalogActionError(reason instanceof Error ? reason.message : "商品資料建立失敗。");
    }
  };
  const catalogAction = preflight.data?.execution_mode === "simulation" ? "建立 Simulation 商品資料" : "立即更新商品資料";
  return (
    <MotionConfig reducedMotion="user">
    <div className="page-stack">
      <header className="page-header"><div><h1>策略管理</h1><p>建立進場計畫、組合市場訊號，並定義每個新部位收到的初始風險規則。</p></div><button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><CirclePlus size={17} /> 建立策略</button></header>
      <RuntimeModeBanner />
      <AnimatePresence>
        {catalog.error && <motion.div key="catalog-error" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}><AlertTriangle size={17} /> 商品快取尚未建立，無法選擇交易商品。<button type="button" className="btn btn-outline" onClick={refreshCatalog}>{catalogAction}</button></motion.div>}
        {catalog.data?.stale && <motion.div key="catalog-stale" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}><AlertTriangle size={17} /> 商品快取已過期（最近更新：{new Date(catalog.data.refreshed_at).toLocaleString("zh-TW")}）。換算與新選擇已封鎖。<button type="button" className="btn btn-outline" onClick={refreshCatalog}>{catalogAction}</button></motion.div>}
        {catalogActionError && <motion.div key="catalog-action-error" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}><AlertTriangle size={17} />{catalogActionError}</motion.div>}
        {error && <motion.div key="strategies-error" className="error-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}>策略 API 無法使用。畫面不會用假資料替代，所有操作已停用。</motion.div>}
        {isLoading && <motion.div key="strategies-loading" className="loading-state" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: .2, ease: "easeOut" }}>正在讀取策略…</motion.div>}
      </AnimatePresence>
      {!error && strategies && <div className="strategy-workspace"><StrategyList strategies={strategies} selectedId={creating ? null : selected?.id ?? null} onSelect={(id) => { setSelectedId(id); setCreating(false); }} onCreate={() => setCreating(true)} /><div className="strategy-main">{creating ? <motion.div key="new" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18, ease: "easeOut" }}><StrategyEditor onSaved={refresh} catalog={catalog.data?.items ?? []} catalogStale={catalog.data?.stale ?? true} allowedInstruments={risk.data?.allowed_instruments} /></motion.div> : selected ? <motion.div key={`${selected.id}-${selected.updated_at}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18, ease: "easeOut" }}><StrategyEditor strategy={selected} onSaved={refresh} catalog={catalog.data?.items ?? []} catalogStale={catalog.data?.stale ?? true} allowedInstruments={risk.data?.allowed_instruments} /><Decisions strategyId={selected.id} /></motion.div> : <div className="panel empty-state">建立第一個策略，開始定義進場訊號與預設部位規則。</div>}</div></div>}
    </div>
    </MotionConfig>
  );
}
