"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, CandlestickChart, CirclePlus, RotateCcw, Save, ShieldAlert, ShieldCheck, Trash2, TrendingDown, XCircle } from "lucide-react";

import ExpressionEditor, { type SignalExpression } from "@/components/ExpressionEditor";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import {
  ApiError,
  amendLogicalPositionStop,
  attachLogicalPositionProtection,
  closeLogicalPosition,
  createLogicalPositionCloseCondition,
  deleteLogicalPositionCloseCondition,
  getLogicalPositionChart,
  listLogicalPositions,
  moveLogicalPositionToBreakEven,
  reduceLogicalPosition,
  updateLogicalPositionCloseCondition,
  validateSignal,
  type LogicalPositionChartResponse,
  type LogicalPositionCloseCondition,
  type LogicalPositionUnit,
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

function PositionList({ positions, selectedId, onSelect }: { positions: LogicalPositionUnit[]; selectedId?: string; onSelect: (id: string) => void }) {
  return (
    <aside className="position-list panel">
      <div className="panel-heading"><div><h2>邏輯部位單位</h2><p>{positions.length} 個獨立管理單位</p></div></div>
      <div className="position-list-items">
        {positions.map((position) => (
          <button type="button" className={`position-list-item ${selectedId === position.id ? "selected" : ""}`} key={position.id} onClick={() => onSelect(position.id)}>
            <span className={`side-mark ${position.side === "long" ? "long" : "short"}`} />
            <span><strong>{position.inst_id}</strong><small>{position.side === "long" ? "做多" : "做空"} · 剩餘 {number(position.remaining_quantity)}</small><small className="mono">{position.id}</small></span>
            <span className={`badge ${activeStatuses.has(position.status) ? "info" : ""}`}>{position.status}</span>
          </button>
        ))}
        {!positions.length && <div className="empty-state">目前沒有邏輯部位單位。</div>}
      </div>
    </aside>
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
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const currentPrice = chart.data?.overlays?.find((overlay) => overlay.kind === "current")?.price;
  const remaining = position.remaining_quantity ?? 0;
  const direction = position.side === "short" ? -1 : 1;
  const pnlPct = currentPrice && position.entry_price ? direction * (currentPrice - position.entry_price) / position.entry_price * 100 : null;
  const okx = object(position.okx_net_position);
  const command = async (name: string, action: () => Promise<unknown>) => { setBusyAction(name); setError(""); try { await action(); await refresh(); await chart.mutate(); } catch (caught) { setError(errorMessage(caught)); } finally { setBusyAction(""); } };
  const close = () => { if (!confirm(`平倉邏輯單位 ${position.id} 的全部剩餘數量 ${number(remaining)}？\n\n真實部位只會在 OKX 成交確認後標記為已平倉。`)) return; return command("close", () => closeLogicalPosition(position.id, { confirm: true, reason: "operator dashboard close" })); };
  const reduce = () => { const quantity = Number(reduceQuantity); if (!(quantity > 0 && quantity < remaining)) { setError("減倉數量必須大於 0 且小於目前剩餘數量。"); return; } if (!confirm(`送出 ${number(quantity)} 的部分減倉請求？\n\n數量只會依 OKX 確認成交更新。`)) return; return command("reduce", () => reduceLogicalPosition(position.id, { confirm: true, quantity, reason: "operator dashboard reduce" })); };
  const protect = () => { if (!confirm("重新驗證或建立此單位的 OKX 停損保護？")) return; return command("protect", () => attachLogicalPositionProtection(position.id, { confirm: true })); };
  const breakEven = () => { const input = prompt("要鎖定多少獲利百分比？輸入 0 代表進場價，最大 5。", "0"); if (input === null) return; const pct = Number(input); if (!Number.isFinite(pct) || pct < 0 || pct > 5) { setError("保本鎖利百分比必須介於 0 到 5。"); return; } const stop = position.close_conditions?.find((condition) => condition.purpose === "stop_loss" && condition.enabled); if (!stop) { setError("找不到已啟用的停損條件。"); return; } if (!confirm(`將 OKX 保護停損移至進場價並鎖定 ${pct}%？`)) return; return command("break-even", () => moveLogicalPositionToBreakEven(position.id, { confirm: true, condition_id: stop.id, lock_in_pct: pct / 100, reason: "operator dashboard break-even" })); };
  return (
    <div className="position-detail">
      <section className="panel position-hero">
        <div className="position-title"><div><div className="status-row"><h2>{position.inst_id}</h2><span className={`badge ${position.side === "long" ? "success" : "danger"}`}>{position.side === "long" ? "做多 LONG" : "做空 SHORT"}</span><span className="badge info">{position.status}</span></div><p className="mono">Maybech 單位：{position.id}</p></div><div className="position-metric"><small>未實現損益估算</small><strong className={pnlPct != null && pnlPct >= 0 ? "positive" : "negative"}>{pnlPct == null ? "資料不足" : `${pnlPct >= 0 ? "+" : ""}${number(pnlPct, 2)}%`}</strong></div></div>
        <div className="metric-grid"><div><small>進場價</small><strong>{number(position.entry_price)}</strong></div><div><small>目前價</small><strong>{number(currentPrice)}</strong></div><div><small>原始數量</small><strong>{number(position.opened_quantity)}</strong></div><div><small>剩餘數量</small><strong>{number(position.remaining_quantity)}</strong></div><div><small>來源</small><strong>{position.source}{position.strategy_id ? ` · ${position.strategy_id}` : ""}</strong></div></div>
      </section>

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
        <div className="reduce-control"><label className="field"><span>部分減倉數量</span><input type="number" min="0" max={remaining} step="any" value={reduceQuantity} onChange={(event) => setReduceQuantity(event.target.value)} /></label><button type="button" className="btn btn-danger" disabled={Boolean(busyAction) || position.status !== "open"} onClick={reduce}><TrendingDown size={16} /> {busyAction === "reduce" ? "等待確認…" : "確認部分減倉"}</button><button type="button" className="btn btn-danger" disabled={Boolean(busyAction) || position.status !== "open"} onClick={close}><XCircle size={16} /> {busyAction === "close" ? "等待確認…" : "確認全部平倉"}</button></div>
      </section>
      {error && <div className="error-state"><AlertTriangle size={17} /> {error}</div>}

      <section className="panel"><div className="panel-heading"><div><h2>確認成交與稽核證據</h2><p>委託送出不等於成交；以下 allocation 才會改變邏輯數量。</p></div></div><div className="evidence-grid">{position.allocations?.map((allocation, index) => { const item = object(allocation); return <article key={String(item.id ?? index)}><span className="badge info">{String(item.action ?? "fill")}</span><strong>{number(Number(item.quantity))} @ {number(Number(item.price))}</strong><small className="mono">{String(item.exchange_order_id ?? "")}</small></article>; })}{!position.allocations?.length && <div className="empty-state">尚無可顯示的確認成交 allocation。</div>}</div></section>
    </div>
  );
}

export default function PositionsPage() {
  const { data, error, mutate, isLoading } = useSWR("logical-positions", () => listLogicalPositions("all"), { refreshInterval: 5000 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = data?.find((position) => position.id === selectedId) ?? data?.find((position) => activeStatuses.has(position.status)) ?? data?.[0];
  return (
    <div className="page-stack">
      <header className="page-header"><div><h1>部位管理</h1><p>逐一管理 Maybech 邏輯部位單位，並與 OKX 合併後的淨部位分開檢視。</p></div></header>
      <RuntimeModeBanner />
      {error && <div className="error-state">邏輯部位 API 無法使用。畫面不會使用假資料，所有交易操作已停用。</div>}
      {isLoading && <div className="loading-state">正在讀取邏輯部位…</div>}
      {!error && data && <div className="position-workspace"><PositionList positions={data} selectedId={selected?.id} onSelect={setSelectedId} />{selected ? <PositionDetail key={`${selected.id}-${selected.updated_at}`} position={selected} refresh={mutate} /> : <div className="panel empty-state">目前沒有可管理的邏輯部位單位。</div>}</div>}
    </div>
  );
}
