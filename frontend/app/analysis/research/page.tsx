"use client";

import { AlertTriangle, BarChart3, Database, ShieldAlert } from "lucide-react";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";

import InstrumentSelector from "@/components/InstrumentSelector";
import KlineChart, { type KlineChartOverlay } from "@/components/KlineChart";
import { getMarketCandles, getRiskLimits, getSupportResistanceAnalysis, listInstruments, listLogicalPositions, listStrategies, promotePositionRiskStop, promoteStrategyRiskStop, quoteInstrumentRisk, type InstrumentRiskQuoteRequest, type SupportResistanceAnalysisResponse } from "@/lib/api";
import { formatPrice } from "@/lib/price-format";

const bars = ["1m", "5m", "15m", "1H", "4H", "1D"];
const format = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? "—" : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(value);
const evidenceNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const researchStateLabel = (s?: string | null) => ({
  proposed: "建議",
  "manual-review": "人工檢查",
  invalidated: "已失效",
  active: "有效",
  partial: "部分",
  unavailable: "不可用",
  stale: "資料過期",
  missing: "缺少",
} as Record<string, string>)[String(s)] ?? String(s ?? "");
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
type ResearchLevel = NonNullable<SupportResistanceAnalysisResponse["levels"]>[number];

function RiskSizing({ instrument, bar, latestPrice, selectedStop, selectedLevel, analysisState, evaluatedAt, entryFeePct, exitFeePct, slippagePct, pricePrecision, onPreview }: { instrument: string; bar: string; latestPrice?: number | null; selectedStop?: number | null; selectedLevel?: ResearchLevel | null; analysisState: string; evaluatedAt?: string; entryFeePct: string; exitFeePct: string; slippagePct: string; pricePrecision?: number | null; onPreview?: (preview: { entry: number; stop: number } | null) => void }) {
  const [mode, setMode] = useState<"fixed_loss" | "chart_anchored">("chart_anchored");
  const [side, setSide] = useState<"long" | "short">("long");
  const [entry, setEntry] = useState("");
  const [loss, setLoss] = useState("100");
  const [notional, setNotional] = useState("3000");
  const [stop, setStop] = useState("");
  const [positionId, setPositionId] = useState("");
  const [strategyId, setStrategyId] = useState("");
  const [promotionMessage, setPromotionState] = useState("");
  const positions = useSWR("analysis-open-positions", () => listLogicalPositions("open"));
  const strategies = useSWR("analysis-strategies", listStrategies);
  const eligiblePositions = (positions.data ?? []).filter((item) => item.inst_id === instrument && item.status === "open");
  const eligibleStrategies = (strategies.data ?? []).filter((item) => item.target_instruments?.length === 1 && item.target_instruments[0] === instrument);
  const selectedPosition = eligiblePositions.find((item) => item.id === positionId);
  const selectedStrategy = eligibleStrategies.find((item) => item.id === strategyId);
  const strategyMetadata = object(selectedStrategy?.metadata);
  const strategyPrices = object(strategyMetadata.sizing_reference_prices);
  const targetEntry = selectedPosition ? String(selectedPosition.entry_price) : selectedStrategy ? String(strategyPrices[instrument] ?? "") : "";
  const targetSide = selectedPosition?.side ?? strategyMetadata.position_side;
  const effectiveEntry = targetEntry || entry || (latestPrice ? String(latestPrice) : "");
  const effectiveSide = targetSide === "short" ? "short" : targetSide === "long" ? "long" : side;
  const effectiveStop = stop || (selectedStop ? String(selectedStop) : "");
  const evidenceConflict = selectedLevel?.evidence?.btc_regime_alignment === "conflicting";
  const researchSelected = selectedStop != null;
  const proposalEligible = !researchSelected || Boolean(selectedLevel && selectedLevel.state === "active" && analysisState === "fresh" && !evidenceConflict);
  const promotionState = !proposalEligible ? "manual-review: selected research is stale, missing, invalidated, or conflicts with BTC regime" : promotionMessage;
  const ready = Boolean(effectiveEntry && loss && (mode === "fixed_loss" ? notional : effectiveStop)) && proposalEligible;
  const payload: InstrumentRiskQuoteRequest = { mode, entry_price: effectiveEntry, side: effectiveSide, allowed_loss_usdt: loss, position_notional_usdt: mode === "fixed_loss" ? notional : null, stop_price: mode === "chart_anchored" ? effectiveStop : null, timeframe: bar, entry_fee_rate: String(Number(entryFeePct) / 100), exit_fee_rate: String(Number(exitFeePct) / 100), slippage_rate: String(Number(slippagePct) / 100), evidence: selectedStop ? { selected_research_level: selectedStop, level_state: selectedLevel?.state ?? "missing", level_score: selectedLevel?.score ?? null, btc_regime_alignment: selectedLevel?.evidence?.btc_regime_alignment ?? "unknown", analysis_state: analysisState, analysis_evaluated_at: evaluatedAt ?? null } : {} };
  const quote = useSWR(ready ? ["risk-size", instrument, JSON.stringify(payload)] : null, () => quoteInstrumentRisk(instrument, payload));
  useEffect(() => {
    if (!onPreview) return;
    if (!quote.data) { onPreview(null); return; }
    const entryValue = Number(quote.data.entry_price);
    const stopValue = Number(quote.data.stop_price);
    onPreview(Number.isFinite(entryValue) && Number.isFinite(stopValue) ? { entry: entryValue, stop: stopValue } : null);
    return () => onPreview(null);
  }, [onPreview, quote.data]);
  const promote = async () => {
    if ((!selectedPosition && !selectedStrategy) || !quote.data || !proposalEligible) return;
    const targetLabel = selectedPosition ? `邏輯部位 ${selectedPosition.id}` : `策略 ${selectedStrategy?.id}`;
    if (!confirm(`確定將檢查後的停損 ${formatPrice(Number(quote.data.stop_price), pricePrecision)} 推廣至 ${targetLabel}？`)) return;
    setPromotionState("推廣中…");
    try {
      if (selectedPosition) {
        const stopCondition = selectedPosition.close_conditions?.find((item) => item.purpose === "stop_loss" && item.enabled);
        await promotePositionRiskStop(selectedPosition.id, { ...payload, confirm: true, expected_position_updated_at: selectedPosition.updated_at, condition_id: stopCondition?.id ?? null, expected_condition_updated_at: stopCondition?.updated_at ?? null, reason: "operator promoted reviewed market-structure stop" });
        await positions.mutate();
      } else if (selectedStrategy) {
        await promoteStrategyRiskStop(selectedStrategy.id, { ...payload, confirm: true, expected_updated_at: selectedStrategy.updated_at ?? "", inst_id: instrument });
        await strategies.mutate();
      }
      setPromotionState("已推廣，並完成檢查。");
    } catch (error) { setPromotionState(error instanceof Error ? error.message : "推廣失敗。"); }
  };
  const targetSelected = Boolean(selectedPosition || selectedStrategy);
  return <section className="panel risk-sizing-panel"><div className="panel-heading"><div><h2><ShieldAlert size={20} /> 固定風險部位計算</h2><p>固定損失模式推導停損；圖表錨定模式依停損距離推導最大部位。</p></div></div><div className="risk-sizing-grid"><label className="field"><span>模式</span><select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="chart_anchored">圖表錨定停損</option><option value="fixed_loss">固定損失停損</option></select></label><label className="field"><span>方向</span><select disabled={targetSelected} value={effectiveSide} onChange={(event) => setSide(event.target.value as typeof side)}><option value="long">多單</option><option value="short">空單</option></select></label><label className="field"><span>進場價</span><input disabled={targetSelected} type="number" min="0" step="any" value={targetSelected ? targetEntry : entry} placeholder={latestPrice ? String(latestPrice) : "輸入價格"} onChange={(event) => setEntry(event.target.value)} /></label><label className="field"><span>可接受損失 USDT</span><input type="number" min="0" step="any" value={loss} onChange={(event) => setLoss(event.target.value)} /></label>{mode === "fixed_loss" ? <label className="field"><span>目標名目 USDT</span><input type="number" min="0" step="any" value={notional} onChange={(event) => setNotional(event.target.value)} /></label> : <label className="field"><span>圖表停損價</span><input type="number" min="0" step="any" value={stop} placeholder={selectedStop ? String(selectedStop) : "點選下方層級"} onChange={(event) => setStop(event.target.value)} /></label>}</div>{quote.error && <div className="error-state"><AlertTriangle size={16} />目前輸入無法形成安全且符合 lot size 的部位。</div>}{quote.isLoading && <div className="loading-state">計算中…</div>}{quote.data && <div className="risk-sizing-result"><div><small>停損價</small><strong>{quote.data.stop_price}</strong></div><div><small>停損距離</small><strong>{format(Number(quote.data.stop_distance_pct) * 100, 2)}%</strong></div><div><small>最大名目</small><strong>{quote.data.estimated_notional_usdt} USDT</strong></div><div><small>OKX 數量</small><strong>{quote.data.api_quantity_contracts} 口</strong></div><div><small>估算損失</small><strong>{quote.data.estimated_loss_usdt} USDT</strong></div><div><small>未使用風險</small><strong>{quote.data.unused_risk_usdt} USDT</strong></div></div>}<div className="sub-editor"><div className="risk-sizing-grid"><label className="field"><span>推廣至策略預設值</span><select value={strategyId} onChange={(event) => { setStrategyId(event.target.value); setPositionId(""); setPromotionState(""); }}><option value="">未選擇策略</option>{eligibleStrategies.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.enabled ? "已啟用（推廣後將停用）" : "已停用"}</option>)}</select></label><label className="field"><span>推廣至現有邏輯部位</span><select value={positionId} onChange={(event) => { setPositionId(event.target.value); setStrategyId(""); setPromotionState(""); }}><option value="">未選擇部位</option>{eligiblePositions.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.side === "long" ? "做多" : "做空"} · {item.remaining_quantity} 口</option>)}</select></label></div><div className="inline-warning">推廣至策略僅允許唯一相符商品，且會停用已啟用策略以便審閱；推廣至部位要求審閱數量等於目前曝險；所有推廣皆會拒絕過期版本。</div>{selectedStrategy && !targetEntry && <div className="error-state">此策略尚未設定該商品的換算參考價格，請先於策略管理更新。</div>}{promotionState && <div className="analysis-context"><span>{promotionState}</span></div>}<div className="form-actions"><button type="button" className="btn btn-primary" disabled={!targetSelected || !quote.data || !targetEntry} onClick={promote}>推廣已檢查停損</button></div></div><div className="inline-warning">未明確推廣前，僅為不可執行的研究提案。</div></section>;
}

function RiskCostAssumptions({ entryFeePct, exitFeePct, slippagePct, onEntryFee, onExitFee, onSlippage }: { entryFeePct: string; exitFeePct: string; slippagePct: string; onEntryFee: (value: string) => void; onExitFee: (value: string) => void; onSlippage: (value: string) => void }) {
  return <section className="panel"><div className="panel-heading"><div><h2>固定風險成本假設</h2><p>風險預算會先保留進出場手續費與雙側滑價，再計算可承受的價格損失與 lot-aligned 部位。</p></div></div><div className="risk-sizing-grid">
    <label className="field"><span>進場手續費</span><span className="input-with-suffix"><input type="number" min="0" max="2" step="0.001" value={entryFeePct} onChange={(event) => onEntryFee(event.target.value)} /><small>%</small></span></label>
    <label className="field"><span>出場手續費</span><span className="input-with-suffix"><input type="number" min="0" max="2" step="0.001" value={exitFeePct} onChange={(event) => onExitFee(event.target.value)} /><small>%</small></span></label>
    <label className="field"><span>每側滑價</span><span className="input-with-suffix"><input type="number" min="0" max="2" step="0.001" value={slippagePct} onChange={(event) => onSlippage(event.target.value)} /><small>%</small></span></label>
  </div></section>;
}

function ResearchLevelCard({ level, selected, onSelect, pricePrecision }: { level: ResearchLevel; selected: boolean; onSelect: () => void; pricePrecision?: number | null }) {
  const volume = evidenceNumber(level.evidence?.volume_ratio);
  const wick = evidenceNumber(level.evidence?.wick_ratio);
  const distance = evidenceNumber(level.evidence?.invalidation_distance_pct);
  const conflicting = level.evidence?.btc_regime_alignment === "conflicting";
  return <article className={`${level.kind} ${selected ? "selected" : ""}`} onClick={onSelect}>
    <div><span className={`badge ${level.kind === "support" ? "success" : "danger"}`}>{level.kind === "support" ? "支撐" : "壓力"}</span><strong>{formatPrice(level.price, pricePrecision)}</strong></div>
    <div className="status-row"><span className={`badge ${level.state === "invalidated" ? "danger" : "success"}`}>{researchStateLabel(level.state)}</span>{selected && <span className="badge info">{researchStateLabel("proposed")}</span>}{conflicting && <span className="badge danger">{researchStateLabel("manual-review")}</span>}</div>
    <dl><div><dt>證據分數</dt><dd>{format(level.score * 100, 0)}%</dd></div><div><dt>觸碰次數</dt><dd>{level.touches}</dd></div><div><dt>成交量比</dt><dd>{format(volume)}×</dd></div><div><dt>影線比</dt><dd>{format(wick == null ? null : wick * 100, 1)}%</dd></div><div><dt>失效距離</dt><dd>{format(distance == null ? null : distance * 100, 2)}%</dd></div></dl>
    <button type="button" className="btn btn-outline" onClick={(event) => { event.stopPropagation(); onSelect(); }}>{selected ? "取消作為停損錨點" : "用作停損錨點"}</button>
  </article>;
}

export default function AnalysisResearchPage() {
  return <Suspense fallback={<div className="loading-state">正在載入…</div>}><AnalysisResearchContent /></Suspense>;
}

function AnalysisResearchContent() {
  const searchParams = useSearchParams();
  const initialInstrument = searchParams.get("instrument")?.trim().toUpperCase() || "BTC-USDT-SWAP";
  const [draft, setDraft] = useState(initialInstrument);
  const [instrument, setInstrument] = useState(initialInstrument);
  const [bar, setBar] = useState("15m");
  const [selectedStop, setSelectedStop] = useState<number | null>(null);
  const [entryFeePct, setEntryFeePct] = useState("0.05");
  const [exitFeePct, setExitFeePct] = useState("0.05");
  const [slippagePct, setSlippagePct] = useState("0.05");
  const [riskPreview, setRiskPreview] = useState<{ entry: number; stop: number } | null>(null);
  const limit = 200;
  const catalog = useSWR("instrument-metadata", listInstruments);
  const risk = useSWR("risk-limits", getRiskLimits);
  const analysis = useSWR(["support-resistance", instrument, bar], () => getSupportResistanceAnalysis(instrument, { bar, limit }), { refreshInterval: 30_000 });
  const candles = useSWR(["analysis-candles", instrument, bar], () => getMarketCandles(instrument, { bar, limit }), { refreshInterval: 30_000 });
  const sortedLevels = useMemo(() => [...(analysis.data?.levels ?? [])].sort((a, b) => b.score - a.score), [analysis.data?.levels]);
  const selectedLevel = sortedLevels.find((level) => level.price === selectedStop) ?? null;
  const submit = (event: FormEvent) => { event.preventDefault(); const next = draft.trim().toUpperCase(); if (next) { setInstrument(next); setSelectedStop(null); setRiskPreview(null); } };
  const stateClass = analysis.data?.freshness.stale ? "stale" : analysis.data?.status ?? "unavailable";
  const chartOverlays = useMemo(() => {
    const topLevels = sortedLevels.slice(0, 6);
    const shown = selectedLevel && !topLevels.some((level) => level.price === selectedLevel.price) ? [...topLevels, selectedLevel] : topLevels;
    const overlays: KlineChartOverlay[] = shown.map((level) => ({
      kind: level.price === selectedStop ? "selected_level" : level.kind,
      price: level.price,
      label: `${level.kind === "support" ? "支撐" : "壓力"} ${format(level.score * 100, 0)}%`,
      evidenceScore: level.score,
    }));
    if (analysis.data?.latest_price != null) {
      overlays.push({ kind: "current", price: analysis.data.latest_price, label: "目前價" });
    }
    if (riskPreview) {
      overlays.push({ kind: "entry", price: riskPreview.entry, label: "風險試算進場" });
      overlays.push({ kind: "stop_loss", price: riskPreview.stop, label: "風險試算停損" });
    }
    return overlays;
  }, [sortedLevels, selectedLevel, selectedStop, riskPreview, analysis.data?.latest_price]);
  const klineChart = useMemo(() => ({
    inst_id: instrument,
    bar,
    candles: candles.data?.candles,
    fetched_at: candles.data?.fetched_at ?? "",
    overlays: chartOverlays,
  }), [instrument, bar, candles.data, chartOverlays]);
  return <>
    <section className="panel"><form onSubmit={submit} className="analysis-form"><div className="field"><span>OKX 商品</span><InstrumentSelector instruments={catalog.data?.items ?? []} stale={catalog.data?.stale ?? true} value={draft ? [draft] : []} onChange={(next) => setDraft(next[0] ?? "")} eligibleInstruments={risk.data?.allowed_instruments} /><small>可研究所有快取 SWAP；不在 allowlist 的商品不可推廣為實盤策略。</small></div><label className="field"><span>時間週期</span><select value={bar} onChange={(event) => setBar(event.target.value)}>{bars.map((item) => <option key={item}>{item}</option>)}</select></label><button className="btn btn-primary" type="submit" disabled={!draft || !catalog.data || catalog.data.stale}><BarChart3 size={16} /> 分析</button></form></section>
    {catalog.error && <div className="error-state"><AlertTriangle size={17} />商品快取無法使用；市場分析不接受未驗證的自由輸入。</div>}
    {analysis.error && <div className="error-state"><AlertTriangle size={17} />分析 API 無法連線；目前沒有可安全採用的證據。</div>}
    {analysis.isLoading && <div className="loading-state">正在取得 K 線並計算研究證據…</div>}
    {analysis.data && <>
      <section className={`analysis-state ${stateClass}`}><div><strong>{stateClass === "fresh" ? "資料新鮮" : stateClass === "stale" ? "資料過期" : stateClass === "partial" ? "資料不完整" : "資料不可用"}</strong><p>最近 K 線：{analysis.data.freshness.latest_candle_at ? new Date(analysis.data.freshness.latest_candle_at).toLocaleString("zh-TW") : "無"} · 年齡 {format(analysis.data.freshness.age_seconds, 0)} 秒</p></div><span className={`badge ${stateClass === "stale" || stateClass === "unavailable" ? "danger" : "info"}`}>{researchStateLabel(stateClass)}</span></section>
      {(analysis.data.errors ?? []).length > 0 && <div className="analysis-warnings">{analysis.data.errors?.map((error) => <div key={error}><AlertTriangle size={15} />{error}</div>)}</div>}
      <div className="analysis-context"><strong>BTC 市場環境</strong><span>{String(analysis.data.context?.btc_direction ?? "unknown")}</span><small>只調整研究證據權重，不直接控制交易。</small></div>
      <section className="analysis-metrics"><article className="panel"><Database size={20} /><small>可用 / 輸入 K 線</small><strong>{analysis.data.quality.usable_candles} / {analysis.data.quality.input_candles}</strong></article><article className="panel"><BarChart3 size={20} /><small>研究層級</small><strong>{sortedLevels.length}</strong></article><article className="panel"><ShieldAlert size={20} /><small>缺漏 / 重複</small><strong>{analysis.data.quality.missing_candles} / {analysis.data.quality.duplicate_candles}</strong></article><article className="panel"><BarChart3 size={20} /><small>ATR 波動</small><strong>{formatPrice(analysis.data.volatility_atr, analysis.data.price_precision)}</strong></article></section>
      <div className="analysis-chart-layout">
        <section className="panel analysis-chart-panel">
          <div className="panel-heading"><div><h2><BarChart3 size={20} /> K 線與研究層級</h2><p>支撐（綠）／壓力（紅）依證據分數標示；圓圈大小反映證據分數；選取的層級以藍色標出；風險試算的進場與停損會即時疊圖顯示。</p></div></div>
          {candles.error ? <div className="error-state">K 線圖不可用。</div> : !candles.data ? <div className="loading-state">正在繪製 K 線…</div> : <KlineChart chart={klineChart} pricePrecision={analysis.data.price_precision} showLegend={false} ariaLabel={`${instrument} 支撐壓力研究 K 線`} />}
        </section>
        <section className="panel analysis-levels-panel">
          <div className="panel-heading"><div><h2>層級證據</h2><p>選取結構失效層級作為停損錨點；失效、衝突或過期證據會進入人工檢查且不可推廣。</p></div></div>
          <div className="analysis-levels">{sortedLevels.map((level) => <ResearchLevelCard key={`${level.kind}-${level.price}`} level={level} selected={selectedStop === level.price} onSelect={() => setSelectedStop(selectedStop === level.price ? null : level.price)} pricePrecision={analysis.data?.price_precision} />)}{!sortedLevels.length && <div className="empty-state">目前資料沒有形成可顯示的研究層級。</div>}</div>
        </section>
      </div>
      <div className="analysis-boundary"><ShieldAlert size={18} /><div><strong>研究與實盤隔離</strong><p>這些標記不能直接送出訂單，也不會靜默建立平倉規則。實盤價格邏輯必須另行儲存為有版本保護的策略或邏輯部位規則。</p></div></div>
    </>}
    {analysis.data && <RiskCostAssumptions entryFeePct={entryFeePct} exitFeePct={exitFeePct} slippagePct={slippagePct} onEntryFee={setEntryFeePct} onExitFee={setExitFeePct} onSlippage={setSlippagePct} />}
    {analysis.data && <RiskSizing instrument={instrument} bar={bar} latestPrice={analysis.data.latest_price} selectedStop={selectedStop} selectedLevel={selectedLevel} analysisState={stateClass} evaluatedAt={analysis.data.freshness.evaluated_at} entryFeePct={entryFeePct} exitFeePct={exitFeePct} slippagePct={slippagePct} pricePrecision={analysis.data.price_precision} onPreview={setRiskPreview} />}
  </>;
}
