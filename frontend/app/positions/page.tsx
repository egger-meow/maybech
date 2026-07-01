"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, CandlestickChart, CirclePlus, RotateCcw, Save, ShieldAlert, ShieldCheck, Trash2, TrendingDown, XCircle } from "lucide-react";

import ExpressionEditor, { type SignalExpression } from "@/components/ExpressionEditor";
import InstrumentSelector from "@/components/InstrumentSelector";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import {
  ApiError,
  adoptRecoveredLogicalPosition,
  amendLogicalPositionStop,
  attachLogicalPositionProtection,
  closeLogicalPosition,
  createLogicalPositionCloseCondition,
  deleteLogicalPositionCloseCondition,
  getLogicalPositionChart,
  getLivePreflight,
  getMarketCandles,
  listInstruments,
  listLogicalPositions,
  manualOpenPosition,
  moveLogicalPositionToBreakEven,
  reduceLogicalPosition,
  quoteInstrumentSize,
  quoteInstrumentContracts,
  refreshInstruments,
  updateLogicalPositionCloseCondition,
  validateSignal,
  type LogicalPositionChartResponse,
  type LogicalPositionCloseCondition,
  type LogicalPositionUnit,
  type InstrumentMetadataResponse,
} from "@/lib/api";

const activeStatuses = new Set(["pending_open", "open", "reducing", "closing"]);

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function number(value: number | null | undefined, digits = 4): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("zh-TW", { maximumFractionDigits: digits });
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = object(error.info).detail;
    if (typeof detail === "string") return detail;
    const message = object(detail).message;
    if (typeof message === "string") return message;
  }
  return error instanceof Error ? error.message : "操作失敗，請檢查後端狀態。";
}

function purposeLabel(purpose: string): string {
  return ({ stop_loss: "停損", take_profit: "停利", trailing: "移動停損", break_even: "保本", manual_review: "人工檢查", exit: "一般出場" } as Record<string, string>)[purpose] ?? purpose;
}

function stale(timestamp?: string): boolean {
  if (!timestamp) return true;
  const age = Date.now() - new Date(timestamp).getTime();
  return !Number.isFinite(age) || age > 120_000;
}

function ManualOpenForm({ catalog, catalogStale, onCreated }: { catalog: InstrumentMetadataResponse[]; catalogStale: boolean; onCreated: (positionId: string) => Promise<unknown> }) {
  const preflight = useSWR("runtime-preflight", getLivePreflight);
  const [instrument, setInstrument] = useState("");
  const [side, setSide] = useState<"long" | "short">("long");
  const [quantity, setQuantity] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [takeProfit, setTakeProfit] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const quote = useSWR(
    instrument && quantity && entryPrice ? ["manual-size-quote", instrument, quantity, entryPrice, side] : null,
    () => quoteInstrumentSize(instrument, { display_quantity: quantity, entry_price: entryPrice, side, rule_price: stopLoss || null }),
  );
  const selectInstrument = async (next: string[]) => {
    const selected = next[0] ?? "";
    setInstrument(selected);
    setEntryPrice("");
    setError("");
    if (!selected) return;
    try {
      const market = await getMarketCandles(selected, { bar: "1m", limit: 2 });
      const latest = market.candles?.[market.candles.length - 1];
      if (stale(market.fetched_at)) setError("市場價格資料已過期，不會自動填入進場價。");
      else if (latest?.close) setEntryPrice(String(latest.close));
      else setError("市場 API 沒有提供目前價格，請勿把空值當成零。 ");
    } catch (caught) {
      setError(`無法取得目前價格：${errorMessage(caught)}`);
    }
  };
  const submit = async () => {
    if (!quote.data) { setError("商品數量尚未通過 OKX 單位換算，不能建立部位。"); return; }
    if (!stopLoss) { setError("必須設定保護性停損底線。"); return; }
    if (!confirm(`建立 ${instrument} ${side === "long" ? "做多" : "做空"} dry-run 邏輯單位？\n\n顯示幣量：${quantity}\nOKX API：${quote.data.api_quantity_contracts} 口\n估算名目：${quote.data.estimated_notional_usdt} USDT`)) return;
    setBusy(true); setError("");
    try {
      const created = await manualOpenPosition({
        confirm: true,
        inst_id: instrument,
        side,
        display_quantity: quantity,
        entry_price: entryPrice,
        stop_loss_price: stopLoss,
        take_profit_price: takeProfit || null,
      });
      await onCreated(created.id);
      setQuantity(""); setStopLoss(""); setTakeProfit("");
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };
  const dryRun = preflight.data?.execution_mode === "dry_run";
  return (
    <section className="panel manual-open-panel">
      <div className="panel-heading"><div><h2><CirclePlus size={19} /> 手動建立邏輯部位</h2><p>以目前價格預填限價；你仍可修改。此版本只允許 Dry-run 建立，不會送出真實委託。</p></div><span className={`badge ${dryRun ? "success" : "danger"}`}>{dryRun ? "Dry-run 可用" : "非 Dry-run 已封鎖"}</span></div>
      <div className="form-grid">
        <div className="field full"><span>OKX 交易商品</span><InstrumentSelector instruments={catalog} stale={catalogStale} value={instrument ? [instrument] : []} onChange={selectInstrument} /></div>
        <label className="field"><span>方向</span><select value={side} onChange={(event) => setSide(event.target.value as "long" | "short")}><option value="long">做多 Long</option><option value="short">做空 Short</option></select></label>
        <label className="field"><span>操作者幣量（{instrument.split("-")[0] || "基礎幣"}）</span><input type="number" min="0" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label className="field"><span>進場限價（預填目前價）</span><input type="number" min="0" step="any" value={entryPrice} onChange={(event) => setEntryPrice(event.target.value)} /></label>
        <label className="field"><span>保護停損價（必填）</span><input type="number" min="0" step="any" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} /></label>
        <label className="field"><span>停利價（選填）</span><input type="number" min="0" step="any" value={takeProfit} onChange={(event) => setTakeProfit(event.target.value)} /></label>
      </div>
      {quote.data && <div className="manual-open-quote"><span><small>顯示幣量</small><strong>{quote.data.display_quantity} {quote.data.display_currency}</strong></span><span><small>OKX API 數量</small><strong>{quote.data.api_quantity_contracts} 口</strong></span><span><small>估算名目</small><strong>{quote.data.estimated_notional_usdt} USDT</strong></span><span><small>停損估算</small><strong className={Number(quote.data.estimated_pnl_usdt) < 0 ? "negative" : ""}>{quote.data.estimated_pnl_usdt == null ? "輸入停損後顯示" : `${quote.data.estimated_pnl_usdt} USDT`}</strong></span></div>}
      {quote.error && <div className="error-state">目前數量無法安全換算成 OKX 合約口數。</div>}
      {error && <div className="error-state"><AlertTriangle size={16} /> {error}</div>}
      <div className="form-actions"><button type="button" className="btn btn-primary" disabled={busy || !dryRun || !quote.data || !stopLoss} onClick={submit}>{busy ? "建立中…" : "確認建立 Dry-run 單位"}</button></div>
    </section>
  );
}

function PositionList({ positions, selectedId, onSelect }: { positions: LogicalPositionUnit[]; selectedId?: string; onSelect: (id: string) => void }) {
  return (
    <aside className="position-list panel">
      <div className="panel-heading"><div><h2>邏輯部位單位</h2><p>{positions.length} 個獨立管理單位</p></div></div>
      <div className="position-list-items">
        {positions.map((position) => <PositionListItem key={position.id} position={position} selected={selectedId === position.id} onSelect={onSelect} />)}
        {!positions.length && <div className="empty-state">目前沒有邏輯部位單位。</div>}
      </div>
    </aside>
  );
}

function PositionListItem({ position, selected, onSelect }: { position: LogicalPositionUnit; selected: boolean; onSelect: (id: string) => void }) {
  const metadata = object(position.metadata);
  const remaining = position.remaining_quantity ?? 0;
  const quote = useSWR(
    remaining > 0 && position.entry_price > 0 ? ["position-display-quote", position.id, remaining, position.entry_price] : null,
    () => quoteInstrumentContracts(position.inst_id, { api_quantity_contracts: String(remaining), entry_price: String(position.entry_price), side: position.side as "long" | "short", rule_price: null }),
  );
  return (
    <button type="button" className={`position-list-item ${selected ? "selected" : ""}`} onClick={() => onSelect(position.id)}>
      <span className={`side-mark ${position.side === "long" ? "long" : "short"}`} />
      <span><strong>{position.inst_id}</strong><small>{position.side === "long" ? "做多" : "做空"} · {quote.data ? `剩餘 ${quote.data.display_quantity} ${quote.data.display_currency} · API ${quote.data.api_quantity_contracts} 口` : `API 剩餘 ${number(position.remaining_quantity)} 口（顯示幣量不可用）`}</small><small className="mono">{position.id}</small></span>
      <span className="status-column"><span className={`badge source-${position.source}`}>{position.source}</span>{metadata.requires_manual_review === true && <span className="badge danger">需人工對帳</span>}<span className={`badge ${activeStatuses.has(position.status) ? "info" : ""}`}>{position.status}</span></span>
    </button>
  );
}

function MiniChart({ chart }: { chart: LogicalPositionChartResponse }) {
  const width = 900; const height = 260; const pad = 26;
  const candles = chart.candles ?? [];
  const prices = [...candles.flatMap((candle) => [candle.high, candle.low]), ...(chart.overlays ?? []).map((overlay) => overlay.price)];
  if (!candles.length || !prices.length) return <div className="empty-state">目前沒有可繪製的 K 線資料。</div>;
  const min = Math.min(...prices); const max = Math.max(...prices); const span = Math.max(max - min, .000001);
  const y = (price: number) => pad + ((max - price) / span) * (height - pad * 2);
  const step = (width - pad * 2) / candles.length; const bodyWidth = Math.max(2, Math.min(10, step * .6));
  const overlayColors: Record<string, string> = { entry: "#3b82f6", current: "#8b5cf6", stop_loss: "#ef4444", take_profit: "#10b981", break_even: "#f59e0b", execution: "#64748b" };
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${chart.inst_id} 部位 K 線與規則價位`}>
        <rect width={width} height={height} rx="16" fill="var(--bg-primary)" />
        {candles.map((candle, index) => { const x = pad + step * index + step / 2; const up = candle.close >= candle.open; const color = up ? "#10b981" : "#ef4444"; return <g key={candle.timestamp}><line x1={x} x2={x} y1={y(candle.high)} y2={y(candle.low)} stroke={color} strokeWidth="1.5" /><rect x={x - bodyWidth / 2} y={Math.min(y(candle.open), y(candle.close))} width={bodyWidth} height={Math.max(2, Math.abs(y(candle.open) - y(candle.close)))} rx="1" fill={color} /></g>; })}
        {(chart.overlays ?? []).filter((overlay) => overlay.kind !== "execution").map((overlay, index) => <g key={`${overlay.kind}-${index}`}><line x1={pad} x2={width - pad} y1={y(overlay.price)} y2={y(overlay.price)} stroke={overlayColors[overlay.kind] ?? "#64748b"} strokeWidth="1.5" strokeDasharray="6 4" /><text x={width - pad - 4} y={y(overlay.price) - 5} textAnchor="end" fill={overlayColors[overlay.kind] ?? "#64748b"} fontSize="11" fontWeight="700">{overlay.label} {number(overlay.price)}</text></g>)}
      </svg>
      <div className="chart-legend">{(chart.overlays ?? []).map((overlay, index) => <span key={`${overlay.kind}-${index}`}><i style={{ background: overlayColors[overlay.kind] ?? "#64748b" }} />{overlay.label}: {number(overlay.price)}</span>)}</div>
    </div>
  );
}

function RuleEditor({ position, condition, onSaved, onCancel }: { position: LogicalPositionUnit; condition?: LogicalPositionCloseCondition; onSaved: () => Promise<unknown>; onCancel?: () => void }) {
  const [purpose, setPurpose] = useState(condition?.purpose ?? "exit");
  const [enabled, setEnabled] = useState(condition?.enabled ?? true);
  const [expression, setExpression] = useState<SignalExpression>(() => Object.keys(object(condition?.expression)).length ? object(condition?.expression) : { type: "price_below", symbol: "self", value: 0 });
  const [dirty, setDirty] = useState(!condition);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ownedStop = Boolean(condition && condition.purpose === "stop_loss" && position.protection && ["active", "amending", "canceling"].includes(position.protection.status));
  const rulePrice = !Array.isArray(expression.conditions) && Number(expression.value) > 0 ? String(expression.value) : "";
  const ruleQuote = useSWR(
    rulePrice && (position.remaining_quantity ?? 0) > 0 ? ["rule-pnl-quote", position.id, condition?.id ?? "new", position.remaining_quantity, rulePrice] : null,
    () => quoteInstrumentContracts(position.inst_id, { api_quantity_contracts: String(position.remaining_quantity), entry_price: String(position.entry_price), side: position.side as "long" | "short", rule_price: rulePrice }),
  );
  const change = (next: SignalExpression) => { setExpression(next); setDirty(true); };
  const save = async () => {
    setBusy(true); setError("");
    try {
      const validation = await validateSignal({ expression });
      if (!validation.valid) throw new Error(validation.errors?.join("；") || "規則格式不正確");
      const normalized = validation.normalized ?? expression;
      if (ownedStop && condition) {
        if (!confirm("此停損擁有真實 OKX 保護單。系統會先驗證舊保護，再修改並確認新價位；確定繼續？")) return;
        await amendLogicalPositionStop(position.id, { confirm: true, condition_id: condition.id, expression: normalized, reason: "operator dashboard stop edit" });
      } else if (condition) {
        await updateLogicalPositionCloseCondition(position.id, condition.id, { purpose, enabled, expression: normalized, metadata: condition.metadata });
      } else {
        await createLogicalPositionCloseCondition(position.id, { purpose, enabled, expression: normalized, metadata: {} });
      }
      setDirty(false); await onSaved(); onCancel?.();
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!condition || !confirm(`刪除「${purposeLabel(condition.purpose ?? "exit")}」規則？此動作會留下稽核紀錄。`)) return;
    setBusy(true); setError("");
    try { await deleteLogicalPositionCloseCondition(position.id, condition.id); await onSaved(); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  };
  return (
    <div className="sub-editor">
      <div className="sub-editor-head">
        <label className="field"><span>規則類型</span><select disabled={ownedStop} value={purpose} onChange={(event) => { setPurpose(event.target.value); setDirty(true); }}><option value="stop_loss">停損</option><option value="take_profit">停利</option><option value="trailing">移動停損</option><option value="break_even">保本</option><option value="manual_review">人工檢查</option><option value="exit">一般出場</option></select></label>
        <label className="check-field"><input type="checkbox" disabled={ownedStop} checked={enabled} onChange={(event) => { setEnabled(event.target.checked); setDirty(true); }} /> 啟用</label>
        {ownedStop && <span className="badge warning"><ShieldCheck size={13} /> OKX 保護單專用修改</span>}
        <span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "尚未儲存" : "已儲存"}</span>
      </div>
      <ExpressionEditor value={expression} onChange={change} label={`${purposeLabel(purpose)}條件`} />
      {ruleQuote.data && <div className="rule-pnl-estimate"><span>依剩餘 {ruleQuote.data.display_quantity} {ruleQuote.data.display_currency} 估算</span><strong className={Number(ruleQuote.data.estimated_pnl_usdt) < 0 ? "negative" : "positive"}>{ruleQuote.data.estimated_pnl_usdt} USDT</strong></div>}
      {!rulePrice && <div className="inline-warning">此規則沒有單一絕對價位，無法顯示單值 USDT 損益；實際條件仍會完整評估。</div>}
      {rulePrice && ruleQuote.error && <div className="inline-warning">缺少或無法解讀商品單位 metadata，USDT 損益估算不可用。</div>}
      {error && <div className="error-state"><AlertTriangle size={16} /> {error}</div>}
      <div className="form-actions">
        {onCancel && <button type="button" className="btn btn-outline" disabled={busy} onClick={onCancel}>取消</button>}
        {condition && <button type="button" className="btn btn-danger" disabled={busy || ownedStop} title={ownedStop ? "受保護停損不可直接刪除" : undefined} onClick={remove}><Trash2 size={15} /> 刪除</button>}
        <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}><Save size={15} /> {busy ? "確認中…" : ownedStop ? "驗證並修改停損" : "儲存規則"}</button>
      </div>
    </div>
  );
}

function PositionDetail({ position, refresh }: { position: LogicalPositionUnit; refresh: () => Promise<unknown> }) {
  const chart = useSWR(["position-chart", position.id], () => getLogicalPositionChart(position.id, { bar: "1m", limit: 100 }), { refreshInterval: 15_000 });
  const [newRule, setNewRule] = useState(false);
  const [reduceQuantity, setReduceQuantity] = useState("");
  const [recoveryStop, setRecoveryStop] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const currentPrice = chart.data?.overlays?.find((overlay) => overlay.kind === "current")?.price;
  const remaining = position.remaining_quantity ?? 0;
  const positionQuote = useSWR(
    remaining > 0 && position.entry_price > 0 ? ["position-detail-quote", position.id, remaining, position.entry_price] : null,
    () => quoteInstrumentContracts(position.inst_id, { api_quantity_contracts: String(remaining), entry_price: String(position.entry_price), side: position.side as "long" | "short", rule_price: null }),
  );
  const reduceQuote = useSWR(
    reduceQuantity && position.entry_price > 0 ? ["position-reduce-quote", position.id, reduceQuantity, position.entry_price] : null,
    () => quoteInstrumentSize(position.inst_id, { display_quantity: reduceQuantity, entry_price: String(position.entry_price), side: position.side as "long" | "short", rule_price: null }),
  );
  const recoveryQuote = useSWR(
    recoveryStop && remaining > 0 && position.entry_price > 0
      ? ["recovery-adoption-quote", position.id, recoveryStop]
      : null,
    () => quoteInstrumentContracts(position.inst_id, {
      api_quantity_contracts: String(remaining),
      entry_price: String(position.entry_price),
      side: position.side as "long" | "short",
      rule_price: recoveryStop,
    }),
  );
  const direction = position.side === "short" ? -1 : 1;
  const pnlPct = currentPrice && position.entry_price ? direction * (currentPrice - position.entry_price) / position.entry_price * 100 : null;
  const okx = object(position.okx_net_position);
  const metadata = object(position.metadata);
  const command = async (name: string, action: () => Promise<unknown>) => { setBusyAction(name); setError(""); try { await action(); await refresh(); await chart.mutate(); } catch (caught) { setError(errorMessage(caught)); } finally { setBusyAction(""); } };
  const close = () => { if (!confirm(`平倉邏輯單位 ${position.id} 的全部剩餘數量 ${number(remaining)}？\n\n真實部位只會在 OKX 成交確認後標記為已平倉。`)) return; return command("close", () => closeLogicalPosition(position.id, { confirm: true, reason: "operator dashboard close" })); };
  const reduce = () => { const apiQuantity = Number(reduceQuote.data?.api_quantity_contracts); const displayQuantity = Number(reduceQuantity); const availableDisplay = Number(positionQuote.data?.display_quantity); if (!(apiQuantity > 0 && apiQuantity < remaining && displayQuantity > 0 && displayQuantity < availableDisplay)) { setError("減倉幣量必須大於 0、小於目前剩餘幣量，且能精確換算為 OKX lot size。"); return; } if (!confirm(`送出 ${reduceQuantity} ${positionQuote.data?.display_currency ?? ""}（OKX API ${reduceQuote.data?.api_quantity_contracts} 口）的部分減倉請求？\n\n數量只會依 OKX 確認成交更新。`)) return; return command("reduce", () => reduceLogicalPosition(position.id, { confirm: true, quantity: apiQuantity, reason: "operator dashboard reduce" })); };
  const protect = () => { if (!confirm("重新驗證或建立此單位的 OKX 停損保護？")) return; return command("protect", () => attachLogicalPositionProtection(position.id, { confirm: true })); };
  const adoptRecovery = () => {
    const stopLoss = Number(recoveryStop);
    if (!(stopLoss > 0)) { setError("請輸入有效的停損價格。"); return; }
    if (!confirm(`採納這個外部部位，並在 OKX 建立 ${number(remaining)} 口、觸發價 ${recoveryStop} 的 reduce-only 停損？\n\n只有交易所回報保護單有效後，人工檢查狀態才會解除。`)) return;
    return command("adopt-recovery", () => adoptRecoveredLogicalPosition(position.id, {
      confirm: true,
      stop_loss: stopLoss,
      reason: "operator adopted recovered exposure in Position Management",
    }));
  };
  const breakEven = () => { const input = prompt("要鎖定多少獲利百分比？輸入 0 代表進場價，最大 5。", "0"); if (input === null) return; const pct = Number(input); if (!Number.isFinite(pct) || pct < 0 || pct > 5) { setError("保本鎖利百分比必須介於 0 到 5。"); return; } const stop = position.close_conditions?.find((condition) => condition.purpose === "stop_loss" && condition.enabled); if (!stop) { setError("找不到已啟用的停損條件。"); return; } if (!confirm(`將 OKX 保護停損移至進場價並鎖定 ${pct}%？`)) return; return command("break-even", () => moveLogicalPositionToBreakEven(position.id, { confirm: true, condition_id: stop.id, lock_in_pct: pct / 100, reason: "operator dashboard break-even" })); };
  return (
    <div className="position-detail">
      <section className="panel position-hero">
        <div className="position-title"><div><div className="status-row"><h2>{position.inst_id}</h2><span className={`badge ${position.side === "long" ? "success" : "danger"}`}>{position.side === "long" ? "做多 LONG" : "做空 SHORT"}</span><span className="badge info">{position.status}</span></div><p className="mono">Maybech 單位：{position.id}</p></div><div className="position-metric"><small>未實現損益估算</small><strong className={pnlPct != null && pnlPct >= 0 ? "positive" : "negative"}>{pnlPct == null ? "資料不足" : `${pnlPct >= 0 ? "+" : ""}${number(pnlPct, 2)}%`}</strong></div></div>
        <div className="metric-grid"><div><small>進場價</small><strong>{number(position.entry_price)}</strong></div><div><small>目前價</small><strong>{number(currentPrice)}</strong></div><div><small>剩餘操作者幣量</small><strong>{positionQuote.data ? `${positionQuote.data.display_quantity} ${positionQuote.data.display_currency}` : "資料不足"}</strong></div><div><small>OKX API 原始數量</small><strong>{number(position.opened_quantity)} 口</strong></div><div><small>OKX API 剩餘數量</small><strong>{number(position.remaining_quantity)} 口</strong></div><div><small>來源</small><strong>{position.source}{position.strategy_id ? ` · ${position.strategy_id}` : ""}</strong></div></div>
      </section>
      {metadata.requires_manual_review === true && <div className="error-state"><AlertTriangle size={17} /> 此單位需要人工對帳：{String(metadata.reconciliation_review_reason ?? metadata.recovery_reason ?? "來源或數量尚未確認")}。系統不會猜測外部減倉應分配到哪一個邏輯單位。</div>}
      {position.source === "recovery" && metadata.requires_manual_review === true && !metadata.reconciliation_review_reason && (
        <section className="panel danger-zone">
          <div className="panel-heading"><div><h2><ShieldAlert size={20} /> 採納外部部位</h2><p>先設定方向正確的底線停損。Maybech 會核對 OKX 淨數量、建立獨立保護單，再以交易所回報證明保護有效；任何一步失敗都不會解除人工檢查。</p></div><span className="badge danger">尚未受 Maybech 保護</span></div>
          <div className="form-grid">
            <label className="field"><span>目前市場價</span><input value={number(currentPrice)} disabled /></label>
            <label className="field"><span>{position.side === "long" ? "多單停損（必須低於目前價）" : "空單停損（必須高於目前價）"}</span><input type="number" min="0" step="any" value={recoveryStop} onChange={(event) => setRecoveryStop(event.target.value)} placeholder={position.side === "long" ? "例如 2900" : "例如 3200"} />{recoveryQuote.data && <small>若觸發，依進場價估算損益：{recoveryQuote.data.estimated_pnl_usdt ?? "資料不足"} USDT</small>}{recoveryQuote.error && <small className="negative">無法依商品規格估算此停損</small>}</label>
          </div>
          <div className="form-actions"><button type="button" className="btn btn-danger" disabled={Boolean(busyAction) || !recoveryQuote.data || !currentPrice} onClick={adoptRecovery}><ShieldCheck size={16} /> {busyAction === "adopt-recovery" ? "等待 OKX 證明…" : "確認採納並建立停損"}</button></div>
        </section>
      )}

      <section className="distinction-grid">
        <article className="panel unit-card"><h3>Maybech 邏輯單位</h3><p>規則、數量、稽核與委託識別都屬於這一個進場單位。</p><dl><div><dt>Client Order ID</dt><dd className="mono">{position.client_order_id || "尚無"}</dd></div><div><dt>Exchange Order ID</dt><dd className="mono">{position.exchange_order_id || "尚無"}</dd></div></dl></article>
        <article className="panel okx-card"><h3>OKX 淨部位快照</h3><p>交易所可能把多個 Maybech 單位合併顯示；此區只供對照，不擁有規則。</p>{position.okx_net_position ? <dl><div><dt>淨數量</dt><dd>{String(okx.pos ?? okx.position ?? "未知")}</dd></div><div><dt>平均價</dt><dd>{String(okx.avgPx ?? okx.average_price ?? "未知")}</dd></div><div><dt>對帳狀態</dt><dd>{String(object(position.reconciliation).state ?? "未知")}</dd></div></dl> : <div className="empty-state">沒有相符或足夠新鮮的 OKX 淨部位資料。</div>}</article>
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2><CandlestickChart size={20} /> 部位價格脈絡</h2><p>進場、目前價、停損、停利與已確認成交均來自真實 API 資料。</p></div>{chart.data && <span className={`badge ${stale(chart.data.fetched_at) ? "danger" : "success"}`}>{stale(chart.data.fetched_at) ? "資料過期" : "資料新鮮"}</span>}</div>
        {chart.error ? <div className="error-state">K 線 API 無法使用，畫面不會推測目前價格。</div> : !chart.data ? <div className="loading-state">讀取 K 線與價位標記…</div> : <MiniChart chart={chart.data} />}
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2>擁有的交易所保護</h2><p>每個仍有真實曝險的邏輯單位應擁有一筆數量完全相符的 OKX 保護停損。</p></div>{position.protection?.status === "active" ? <span className="protection-state good"><ShieldCheck size={19} /> 保護有效</span> : <span className="protection-state bad"><ShieldAlert size={19} /> {position.protection?.status ?? "沒有保護"}</span>}</div>
        {position.protection ? <div className="metric-grid"><div><small>停損價</small><strong>{number(position.protection.stop_loss)}</strong></div><div><small>保護數量</small><strong>{number(position.protection.quantity)}</strong></div><div><small>Algo ID</small><strong className="mono">{position.protection.algo_id}</strong></div><div><small>觸發委託</small><strong className="mono">{position.protection.trigger_order_id || "尚未觸發"}</strong></div></div> : <div className="error-state">此單位沒有可見的 OKX 保護紀錄。</div>}
        <div className="form-actions"><button type="button" className="btn btn-outline" disabled={Boolean(busyAction) || position.status !== "open"} onClick={protect}><RotateCcw size={15} /> {busyAction === "protect" ? "驗證中…" : "重試／驗證保護"}</button><button type="button" className="btn btn-outline" disabled={Boolean(busyAction) || position.status !== "open" || position.protection?.status !== "active"} onClick={breakEven}><ShieldCheck size={15} /> {busyAction === "break-even" ? "修改中…" : "移至保本／鎖利"}</button></div>
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2>單位專屬出場規則</h2><p>支援 AND、OR 與括號群組；規則只管理這一個 Maybech 邏輯單位。</p></div><button type="button" className="btn btn-outline" onClick={() => setNewRule(true)}><CirclePlus size={15} /> 新增規則</button></div>
        <div className="rule-stack">{position.close_conditions?.map((condition) => <RuleEditor key={`${condition.id}-${condition.updated_at}`} position={position} condition={condition} onSaved={refresh} />)}{newRule && <RuleEditor position={position} onSaved={refresh} onCancel={() => setNewRule(false)} />}{!position.close_conditions?.length && !newRule && <div className="error-state">此單位沒有出場規則。真實曝險不應在缺少停損保護時繼續運作。</div>}</div>
      </section>

      <section className="panel danger-zone action-zone">
        <div><h2>減倉／平倉</h2><p>這些操作只針對目前邏輯單位。真實模式會先撤除並確認此單位的保護單，再送出 reduce-only 委託；本地數量只依 OKX 確認成交更新。</p></div>
        <div className="reduce-control"><label className="field"><span>部分減倉幣量（{positionQuote.data?.display_currency ?? "基礎幣"}）</span><input type="number" min="0" max={positionQuote.data?.display_quantity} step="any" value={reduceQuantity} onChange={(event) => setReduceQuantity(event.target.value)} />{reduceQuote.data && <small>OKX API：{reduceQuote.data.api_quantity_contracts} 口 · 約 {reduceQuote.data.estimated_notional_usdt} USDT</small>}{reduceQuote.error && <small className="negative">無法依 lot size 安全換算</small>}</label><button type="button" className="btn btn-danger" disabled={Boolean(busyAction) || position.status !== "open" || !reduceQuote.data} onClick={reduce}><TrendingDown size={16} /> {busyAction === "reduce" ? "等待確認…" : "確認部分減倉"}</button><button type="button" className="btn btn-danger" disabled={Boolean(busyAction) || position.status !== "open"} onClick={close}><XCircle size={16} /> {busyAction === "close" ? "等待確認…" : "確認全部平倉"}</button></div>
      </section>
      {error && <div className="error-state"><AlertTriangle size={17} /> {error}</div>}

      <section className="panel"><div className="panel-heading"><div><h2>確認成交與稽核證據</h2><p>委託送出不等於成交；以下 allocation 才會改變邏輯數量。</p></div></div><div className="evidence-grid">{position.allocations?.map((allocation, index) => { const item = object(allocation); return <article key={String(item.id ?? index)}><span className="badge info">{String(item.action ?? "fill")}</span><strong>{number(Number(item.quantity))} @ {number(Number(item.price))}</strong><small className="mono">{String(item.exchange_order_id ?? "")}</small></article>; })}{!position.allocations?.length && <div className="empty-state">尚無可顯示的確認成交 allocation。</div>}</div></section>
    </div>
  );
}

export default function PositionsPage() {
  const { data, error, mutate, isLoading } = useSWR("logical-positions", () => listLogicalPositions("all"), { refreshInterval: 5000 });
  const catalog = useSWR("instrument-metadata", listInstruments);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const managedPositions = (data ?? []).filter((position) => activeStatuses.has(position.status));
  const selected = managedPositions.find((position) => position.id === selectedId) ?? managedPositions[0];
  const created = async (positionId: string) => { await mutate(); setSelectedId(positionId); };
  const refreshCatalog = async () => { await refreshInstruments(); await catalog.mutate(); };
  return (
    <div className="page-stack">
      <header className="page-header"><div><h1>部位管理</h1><p>逐一管理 Maybech 邏輯部位單位，並與 OKX 合併後的淨部位分開檢視。</p></div></header>
      <RuntimeModeBanner />
      <ManualOpenForm catalog={catalog.data?.items ?? []} catalogStale={catalog.data?.stale ?? true} onCreated={created} />
      {catalog.error && <div className="error-state">OKX 商品快取無法使用，手動建立已停用；畫面不會提供硬編碼商品。</div>}
      {catalog.data?.stale && <div className="error-state"><AlertTriangle size={17} /> OKX 商品快取已過期，數量換算與手動建立已封鎖。<button type="button" className="btn btn-outline" onClick={refreshCatalog}>立即更新商品資料</button></div>}
      {error && <div className="error-state">邏輯部位 API 無法使用。畫面不會使用假資料，所有交易操作已停用。</div>}
      {isLoading && <div className="loading-state">正在讀取邏輯部位…</div>}
      {!error && data && <div className="position-workspace"><PositionList positions={managedPositions} selectedId={selected?.id} onSelect={setSelectedId} />{selected ? <PositionDetail key={`${selected.id}-${selected.updated_at}`} position={selected} refresh={mutate} /> : <div className="panel empty-state">目前沒有需要管理的有效邏輯部位。已平倉與失敗單位不會顯示在操作清單中。</div>}</div>}
    </div>
  );
}
