"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Check, ChevronRight, CirclePlus, Power, Save, Trash2 } from "lucide-react";

import ExpressionEditor, { type SignalExpression } from "@/components/ExpressionEditor";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import {
  ApiError,
  createStrategy,
  createStrategySignal,
  deleteStrategy,
  deleteStrategySignal,
  disableStrategy,
  enableStrategy,
  listPersistedStrategyDecisions,
  listStrategies,
  updateStrategy,
  updateStrategySignal,
  validateSignal,
  type SignalExpression as PersistedSignalExpression,
  type StrategySummary,
} from "@/lib/api";

type CloseRule = { purpose: string; enabled: boolean; expression: SignalExpression; metadata?: Record<string, unknown> };
type Draft = {
  name: string;
  instruments: string;
  side: "long" | "short";
  contracts: string;
  slippagePercent: string;
  entrySignal: SignalExpression;
  closeRules: CloseRule[];
};

const blankDraft = (): Draft => ({
  name: "",
  instruments: "ETH-USDT-SWAP",
  side: "long",
  contracts: "1",
  slippagePercent: "0.5",
  entrySignal: { type: "price_above", symbol: "self", value: 0 },
  closeRules: [
    { purpose: "stop_loss", enabled: true, expression: { type: "price_below", symbol: "self", value: 0 } },
    { purpose: "take_profit", enabled: true, expression: { type: "price_above", symbol: "self", value: 0 } },
  ],
});

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
  const sizes = object(metadata.order_size_contracts);
  const firstInstrument = strategy.target_instruments?.[0] ?? "";
  return {
    name: strategy.name,
    instruments: (strategy.target_instruments ?? []).join(", "),
    side: metadata.position_side === "short" ? "short" : "long",
    contracts: String(sizes[firstInstrument] ?? "1"),
    slippagePercent: String(Number(metadata.max_entry_slippage_pct ?? .005) * 100),
    entrySignal: object(strategy.entry_signal),
    closeRules: rulesFrom(strategy),
  };
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

function StrategyList({ strategies, selectedId, onSelect, onCreate }: {
  strategies: StrategySummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="strategy-list panel">
      <div className="panel-heading">
        <div><h2>策略</h2><p>{strategies.length} 個已儲存策略</p></div>
        <button className="icon-button" type="button" onClick={onCreate} aria-label="建立策略"><CirclePlus size={20} /></button>
      </div>
      <div className="strategy-list-items">
        {strategies.map((strategy) => (
          <button type="button" className={`strategy-list-item ${selectedId === strategy.id ? "selected" : ""}`} key={strategy.id} onClick={() => onSelect(strategy.id)}>
            <span className={`status-dot ${strategy.enabled ? "on" : "off"}`} />
            <span><strong>{strategy.name}</strong><small>{strategy.target_instruments?.join(" · ") || "尚未指定商品"}</small></span>
            <ChevronRight size={17} />
          </button>
        ))}
        {!strategies.length && <div className="empty-state">還沒有策略。建立第一個進場計畫。</div>}
      </div>
    </aside>
  );
}

function ChildSignal({ strategyId, signal, onSaved }: { strategyId: string; signal?: PersistedSignalExpression; onSaved: () => Promise<unknown> }) {
  const [purpose, setPurpose] = useState(signal?.purpose ?? "filter");
  const [expression, setExpression] = useState<SignalExpression>(object(signal?.expression).type || object(signal?.expression).op ? object(signal?.expression) : { type: "price_above", symbol: "BTC-USDT-SWAP", value: 0 });
  const [dirty, setDirty] = useState(!signal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true); setError("");
    try {
      const validation = await validateSignal({ expression });
      if (!validation.valid) throw new Error(validation.errors?.join("；") || "規則格式不正確");
      if (signal) await updateStrategySignal(strategyId, signal.id, { purpose, expression: validation.normalized ?? expression });
      else await createStrategySignal(strategyId, { purpose, expression: validation.normalized ?? expression });
      setDirty(false); await onSaved();
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!signal || !confirm("確定刪除此附加訊號？若策略因此不完整，後端會自動停用策略。")) return;
    setBusy(true); setError("");
    try { await deleteStrategySignal(strategyId, signal.id); await onSaved(); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  };
  return (
    <div className="sub-editor">
      <div className="sub-editor-head">
        <label className="field"><span>用途</span><select value={purpose} onChange={(event) => { setPurpose(event.target.value); setDirty(true); }}><option value="entry">進場條件</option><option value="filter">進場過濾</option><option value="exit">複製至部位的出場條件</option></select></label>
        <span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "尚未儲存" : "已儲存"}</span>
      </div>
      <ExpressionEditor value={expression} onChange={(next) => { setExpression(next); setDirty(true); }} label="附加訊號" />
      {error && <div className="error-state">{error}</div>}
      <div className="form-actions">
        {signal && <button type="button" className="btn btn-danger" disabled={busy} onClick={remove}><Trash2 size={15} /> 刪除</button>}
        <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}><Save size={15} /> {busy ? "儲存中…" : "儲存訊號"}</button>
      </div>
    </div>
  );
}

function StrategyEditor({ strategy, onSaved }: { strategy?: StrategySummary; onSaved: (selectedId?: string) => Promise<unknown> }) {
  const [draft, setDraft] = useState<Draft>(() => strategy ? draftFrom(strategy) : blankDraft());
  const [dirty, setDirty] = useState(!strategy);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newSignal, setNewSignal] = useState(false);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => { setDraft((current) => ({ ...current, [key]: value })); setDirty(true); };
  const instruments = draft.instruments.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);

  const save = async () => {
    setBusy(true); setError("");
    try {
      if (!draft.name.trim()) throw new Error("請輸入策略名稱。");
      if (!instruments.length) throw new Error("至少需要一個交易商品。");
      const expressions = [draft.entrySignal, ...draft.closeRules.map((rule) => rule.expression)];
      const validated = await Promise.all(expressions.map((expression) => validateSignal({ expression })));
      const invalid = validated.find((result) => !result.valid);
      if (invalid) throw new Error(invalid.errors?.join("；") || "規則格式不正確");
      const sizes = Object.fromEntries(instruments.map((instrument) => [instrument, draft.contracts]));
      const payload = {
        name: draft.name.trim(),
        kind: "signal",
        target_instruments: instruments,
        entry_signal: validated[0].normalized ?? draft.entrySignal,
        default_rules: { close_conditions: draft.closeRules.map((rule, index) => ({ ...rule, expression: validated[index + 1].normalized ?? rule.expression })) },
        metadata: { ...object(strategy?.metadata), position_side: draft.side, order_size_contracts: sizes, max_entry_slippage_pct: String(Number(draft.slippagePercent) / 100) },
      };
      const saved = strategy ? await updateStrategy(strategy.id, payload) : await createStrategy({ ...payload, enabled: false });
      setDirty(false); await onSaved(saved.id);
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };

  const toggle = async () => {
    if (!strategy) return;
    if (!strategy.enabled && !confirm("啟用策略後，在實盤已武裝且進場閘門開啟時可能建立真實部位。確定啟用？")) return;
    setBusy(true); setError("");
    try { if (strategy.enabled) await disableStrategy(strategy.id); else await enableStrategy(strategy.id); await onSaved(strategy.id); }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!strategy || !confirm(`永久刪除已停用的策略「${strategy.name}」？已有部位歷史時後端會拒絕。`)) return;
    setBusy(true); setError("");
    try { await deleteStrategy(strategy.id); await onSaved(); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  };
  const updateRule = (index: number, next: CloseRule) => { const rules = [...draft.closeRules]; rules[index] = next; set("closeRules", rules); };
  return (
    <section className="strategy-editor panel">
      <div className="panel-heading">
        <div><h2>{strategy ? strategy.name : "建立新策略"}</h2><p>{strategy ? <span className="mono">{strategy.id}</span> : "新策略一律以停用狀態建立"}</p></div>
        {strategy && <div className="status-row"><span className={`badge ${strategy.enabled ? "success" : "warning"}`}>{strategy.enabled ? "已啟用" : "已停用"}</span><span className={`badge ${strategy.readiness === "ready" ? "success" : "warning"}`}>{strategy.readiness ?? "unknown"}</span></div>}
      </div>
      <div className="form-grid">
        <label className="field"><span>策略名稱</span><input value={draft.name} onChange={(event) => set("name", event.target.value)} /></label>
        <label className="field"><span>方向</span><select value={draft.side} onChange={(event) => set("side", event.target.value as "long" | "short")}><option value="long">做多 Long</option><option value="short">做空 Short</option></select></label>
        <label className="field full"><span>交易商品（逗號分隔）</span><input value={draft.instruments} onChange={(event) => set("instruments", event.target.value)} placeholder="ETH-USDT-SWAP, SOL-USDT-SWAP" /></label>
        <label className="field"><span>每個商品委託口數</span><input type="number" min="0" step="any" value={draft.contracts} onChange={(event) => set("contracts", event.target.value)} /></label>
        <label className="field"><span>最大進場滑價</span><span className="input-with-suffix"><input type="number" min="0" max="5" step="0.1" value={draft.slippagePercent} onChange={(event) => set("slippagePercent", event.target.value)} /><small>%</small></span></label>
        <div className="full"><ExpressionEditor value={draft.entrySignal} onChange={(value) => set("entrySignal", value)} label="主要進場訊號" /></div>
      </div>

      <div className="section-divider"><div><h3>預設部位規則</h3><p>每次進場都會複製一份至新的 Maybech 邏輯部位，不會共用同一筆規則。</p></div><button type="button" className="btn btn-outline" onClick={() => set("closeRules", [...draft.closeRules, { purpose: "exit", enabled: true, expression: { type: "price_below", symbol: "self", value: 0 } }])}><CirclePlus size={15} /> 新增規則</button></div>
      <div className="rule-stack">
        {draft.closeRules.map((rule, index) => (
          <div className="sub-editor" key={index}>
            <div className="sub-editor-head">
              <label className="field"><span>規則類型</span><select value={rule.purpose} onChange={(event) => updateRule(index, { ...rule, purpose: event.target.value })}><option value="stop_loss">停損</option><option value="take_profit">停利</option><option value="trailing">移動停損</option><option value="break_even">保本</option><option value="manual_review">人工檢查</option><option value="exit">一般出場</option></select></label>
              <label className="check-field"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(index, { ...rule, enabled: event.target.checked })} /> 啟用</label>
              <button type="button" className="icon-button danger-ghost" aria-label="移除規則" onClick={() => set("closeRules", draft.closeRules.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={16} /></button>
            </div>
            <ExpressionEditor value={rule.expression} onChange={(expression) => updateRule(index, { ...rule, expression })} label={`${rule.purpose.replaceAll("_", " ")} 條件`} />
          </div>
        ))}
      </div>
      {error && <div className="error-state"><AlertTriangle size={16} /> {error}</div>}
      <div className="form-actions">
        <span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "有尚未儲存的變更" : <><Check size={15} /> 已儲存</>}</span>
        {strategy && <button type="button" className={strategy.enabled ? "btn btn-outline" : "btn btn-danger"} disabled={busy || dirty} onClick={toggle}><Power size={15} /> {strategy.enabled ? "停用策略" : "啟用策略"}</button>}
        <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}><Save size={16} /> {busy ? "儲存中…" : "儲存策略"}</button>
      </div>

      {strategy && <>
        <div className="section-divider"><div><h3>附加訊號</h3><p>Entry／Filter 會與主要訊號以 AND 組合；Exit 會複製至新部位。</p></div><button type="button" className="btn btn-outline" onClick={() => setNewSignal(true)}><CirclePlus size={15} /> 新增訊號</button></div>
        <div className="rule-stack">
          {strategy.signal_expressions?.map((signal) => <ChildSignal key={signal.id} strategyId={strategy.id} signal={signal} onSaved={() => onSaved(strategy.id)} />)}
          {newSignal && <ChildSignal strategyId={strategy.id} onSaved={async () => { setNewSignal(false); return onSaved(strategy.id); }} />}
          {!strategy.signal_expressions?.length && !newSignal && <div className="empty-state">沒有附加訊號；主要進場訊號仍會獨立運作。</div>}
        </div>
        <div className="danger-zone strategy-delete"><div><strong>永久刪除策略</strong><p>只有已停用且沒有任何邏輯部位或舊交易歷史的策略才能刪除。</p></div><button type="button" className="btn btn-danger" disabled={busy || strategy.enabled} onClick={remove}><Trash2 size={15} /> 刪除策略</button></div>
      </>}
    </section>
  );
}

function Decisions({ strategyId }: { strategyId: string }) {
  const { data, error } = useSWR(["strategy-decisions", strategyId], () => listPersistedStrategyDecisions(strategyId, { limit: 20 }), { refreshInterval: 5000 });
  return <section className="panel"><div className="panel-heading"><div><h2>最近決策與證據</h2><p>重新啟動後仍保留的 SQLite 稽核紀錄</p></div></div>{error ? <div className="error-state">無法讀取策略決策。</div> : !data ? <div className="loading-state">讀取中…</div> : data.length ? <div className="decision-grid">{data.map((decision, index) => <article key={decision.id ?? index} className="decision-card"><div className="status-row"><span className={`badge ${decision.allowed ? "success" : "danger"}`}>{decision.allowed ? "允許" : "封鎖"}</span><span className="badge info">{decision.execution_status ?? "未執行"}</span></div><strong>{decision.pair ?? "未知商品"}</strong><p>{decision.reason || "沒有提供原因"}</p><small>{decision.time ? new Date(decision.time).toLocaleString("zh-TW") : "時間未知"}</small></article>)}</div> : <div className="empty-state">尚無策略決策紀錄。</div>}</section>;
}

export default function StrategiesPage() {
  const { data: strategies, error, mutate, isLoading } = useSWR("strategies", listStrategies, { refreshInterval: 10_000 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const selected = strategies?.find((strategy) => strategy.id === selectedId) ?? strategies?.[0];
  const refresh = async (id?: string) => { await mutate(); if (id) setSelectedId(id); else if (id === undefined) setSelectedId(null); setCreating(false); };
  return (
    <div className="page-stack">
      <header className="page-header"><div><h1>策略管理</h1><p>建立進場計畫、組合市場訊號，並定義每個新部位收到的初始風險規則。</p></div><button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><CirclePlus size={17} /> 建立策略</button></header>
      <RuntimeModeBanner />
      {error && <div className="error-state">策略 API 無法使用。畫面不會用假資料替代，所有操作已停用。</div>}
      {isLoading && <div className="loading-state">正在讀取策略…</div>}
      {!error && strategies && <div className="strategy-workspace"><StrategyList strategies={strategies} selectedId={creating ? null : selected?.id ?? null} onSelect={(id) => { setSelectedId(id); setCreating(false); }} onCreate={() => setCreating(true)} /><div className="strategy-main">{creating ? <StrategyEditor key="new" onSaved={refresh} /> : selected ? <><StrategyEditor key={`${selected.id}-${selected.updated_at}`} strategy={selected} onSaved={refresh} /><Decisions strategyId={selected.id} /></> : <div className="panel empty-state">建立第一個策略，開始定義進場訊號與預設部位規則。</div>}</div></div>}
    </div>
  );
}
