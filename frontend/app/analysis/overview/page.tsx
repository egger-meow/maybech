"use client";

import { AlertTriangle, Gauge, RefreshCw, TrendingUp } from "lucide-react";
import useSWR from "swr";

import { getMarketMacroOverview, type MarketMacroOverviewResponse } from "@/lib/api";
import { formatPrice } from "@/lib/price-format";

type FearGreedPoint = NonNullable<MarketMacroOverviewResponse["fear_greed"]["history"]>[number];

const MVRV_CLASSIFICATION_LABEL: Record<string, string> = {
  undervalued: "低估",
  neutral: "中性",
  elevated: "偏熱",
  overheated: "過熱",
};

const MVRV_CLASSIFICATION_HINT: Record<string, string> = {
  undervalued: "市值低於實現價值，歷史上多落在週期底部區間。",
  neutral: "市值與實現價值大致相符，沒有明顯的週期極端訊號。",
  elevated: "市值已明顯高於實現價值，獲利了結風險升高。",
  overheated: "偏離歷史高點區間，過去週期頂部常出現在此區間。",
};

const MVRV_CLASSIFICATION_BADGE: Record<string, string> = {
  undervalued: "success",
  neutral: "info",
  elevated: "warning",
  overheated: "danger",
};

const percent = (value: string | null | undefined, digits = 2): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed) ? "—" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}%`;
};

const fundingPct = (value: string | null | undefined): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed) ? "—" : `${(parsed * 100).toFixed(4)}%`;
};

const compactCcy = (value: string | null | undefined): string => {
  const parsed = Number(value);
  if (value == null || value === "" || !Number.isFinite(parsed)) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 2 }).format(parsed);
};

function changeClass(value: string | null | undefined): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed === 0) return "";
  return parsed > 0 ? "positive" : "negative";
}

function Sparkline({ points, width = 200, height = 44 }: { points: number[]; width?: number; height?: number }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);
  const path = points
    .map((point, index) => {
      const x = index * stepX;
      const y = height - ((point - min) / span) * (height - 4) - 2;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="macro-sparkline" preserveAspectRatio="none" role="img" aria-label="近期趨勢圖">
      <path d={path} fill="none" strokeWidth={2} className="macro-sparkline-path" />
    </svg>
  );
}

function FearGreedMeter({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="fng-meter">
      <div className="fng-track">
        <span className="fng-pointer" style={{ left: `${clamped}%` }} />
      </div>
      <div className="fng-scale">
        <span>極度恐懼</span>
        <span>恐懼</span>
        <span>中性</span>
        <span>貪婪</span>
        <span>極度貪婪</span>
      </div>
    </div>
  );
}

export default function AnalysisOverviewPage() {
  const overview = useSWR("market-macro-overview", getMarketMacroOverview, { refreshInterval: 60_000 });
  const data = overview.data;

  const fearGreedHistory: FearGreedPoint[] = data?.fear_greed?.history ?? [];
  const fearGreedLatest = data?.fear_greed?.latest ?? null;
  const prices = data?.prices ?? [];
  const mvrv = data?.mvrv ?? null;
  const mvrvValue = mvrv?.value != null ? Number(mvrv.value) : null;
  const mvrvClassification = mvrv?.classification ?? null;
  const fundingEntries = data?.funding?.entries ?? [];

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>整體市場氛圍</h2>
            <p>彙整免費公開指標與 OKX 行情，掌握大盤情緒、估值週期與資金費率的整體樣貌。</p>
          </div>
          {data && <span className="badge info"><RefreshCw size={13} /> 每 60 秒自動更新</span>}
        </div>
      </section>

      {overview.error && <div className="error-state"><AlertTriangle size={17} />市場總覽 API 無法連線。</div>}
      {overview.isLoading && <div className="loading-state">正在取得市場總覽資料…</div>}

      {data && (
        <div className="macro-grid">
          <section className="panel macro-card">
            <div className="panel-heading"><h2><TrendingUp size={17} /> BTC／ETH 現貨概況</h2></div>
            {data.funding.unavailable_reason && prices.length === 0 ? (
              <div className="empty-state"><AlertTriangle size={16} />{data.funding.unavailable_reason}</div>
            ) : (
              <div className="metric-grid macro-price-grid">
                {prices.map((row) => (
                  <div key={row.symbol}>
                    <small>{row.symbol.replace("-USDT-SWAP", "")}</small>
                    <strong>{formatPrice(row.last_price != null ? Number(row.last_price) : null)}</strong>
                    <span className={changeClass(row.change_24h_pct)}>{percent(row.change_24h_pct)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel macro-card">
            <div className="panel-heading"><h2><Gauge size={17} /> 恐懼與貪婪指數</h2></div>
            {data.fear_greed.unavailable_reason ? (
              <div className="empty-state"><AlertTriangle size={16} />{data.fear_greed.unavailable_reason}</div>
            ) : fearGreedLatest ? (
              <>
                <div className="macro-headline">
                  <strong>{fearGreedLatest.value}</strong>
                  <span>{fearGreedLatest.classification}</span>
                </div>
                <FearGreedMeter value={fearGreedLatest.value} />
                {fearGreedHistory.length > 1 && (
                  <div className="macro-sparkline-wrap">
                    <Sparkline points={fearGreedHistory.map((point) => point.value)} />
                    <small>過去 {fearGreedHistory.length} 天走勢</small>
                  </div>
                )}
              </>
            ) : (
              <div className="empty-state"><AlertTriangle size={16} />暫無資料。</div>
            )}
          </section>

          <section className="panel macro-card">
            <div className="panel-heading"><h2>MVRV Z-Score</h2></div>
            {mvrv?.unavailable_reason ? (
              <div className="empty-state"><AlertTriangle size={16} />{mvrv.unavailable_reason}</div>
            ) : mvrvValue != null ? (
              <>
                <div className="macro-headline">
                  <strong>{mvrvValue.toFixed(2)}</strong>
                  {mvrvClassification && (
                    <span className={`badge ${MVRV_CLASSIFICATION_BADGE[mvrvClassification] ?? "info"}`}>
                      {MVRV_CLASSIFICATION_LABEL[mvrvClassification] ?? mvrvClassification}
                    </span>
                  )}
                </div>
                {mvrvClassification && <p className="macro-hint">{MVRV_CLASSIFICATION_HINT[mvrvClassification]}</p>}
                {mvrv?.as_of && <small className="macro-as-of">資料日期：{mvrv.as_of}</small>}
              </>
            ) : (
              <div className="empty-state"><AlertTriangle size={16} />暫無資料。</div>
            )}
          </section>

          <section className="panel macro-card">
            <div className="panel-heading"><h2>加權資金費率（BTC／ETH）</h2></div>
            {data.funding.unavailable_reason ? (
              <div className="empty-state"><AlertTriangle size={16} />{data.funding.unavailable_reason}</div>
            ) : (
              <>
                {data.funding.weighted_average_funding_rate != null && (
                  <div className="macro-headline">
                    <strong className={changeClass(data.funding.weighted_average_funding_rate)}>
                      {fundingPct(data.funding.weighted_average_funding_rate)}
                    </strong>
                    <span>未平倉量加權平均</span>
                  </div>
                )}
                <div className="metric-grid">
                  {fundingEntries.map((entry) => (
                    <div key={entry.symbol}>
                      <small>{entry.symbol.replace("-USDT-SWAP", "")}</small>
                      <strong className={changeClass(entry.funding_rate)}>{fundingPct(entry.funding_rate)}</strong>
                      <span>未平倉 {compactCcy(entry.open_interest_ccy)}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
