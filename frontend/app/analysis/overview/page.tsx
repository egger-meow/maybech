"use client";

import { AlertTriangle, Activity, Bitcoin, Gauge, Globe2, RefreshCw, TrendingUp } from "lucide-react";
import useSWR from "swr";

import { getMarketMacroOverview, type MarketMacroOverviewResponse } from "@/lib/api";
import { formatPrice } from "@/lib/price-format";

type FearGreedPoint = NonNullable<MarketMacroOverviewResponse["fear_greed"]["history"]>[number];
type MvrvPoint = NonNullable<MarketMacroOverviewResponse["mvrv"]["history"]>[number];

const MVRV_CLASSIFICATION_LABEL: Record<string, string> = {
  undervalued: "低估",
  neutral: "中性",
  elevated: "偏高",
  overheated: "過熱",
};

const MVRV_CLASSIFICATION_HINT: Record<string, string> = {
  undervalued: "鏈上估值低於長期均衡，適合觀察週期低檔是否形成。",
  neutral: "估值仍在常態區間，先看趨勢與資金費率是否共振。",
  elevated: "估值開始偏高，追價前要確認現貨量能與風險承受度。",
  overheated: "估值進入高溫區，適合提高風控權重並留意週期回撤。",
};

const MVRV_CLASSIFICATION_BADGE: Record<string, string> = {
  undervalued: "success",
  neutral: "info",
  elevated: "warning",
  overheated: "danger",
};

const percent = (value: string | null | undefined, digits = 2): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed) ? "--" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}%`;
};

const fundingPct = (value: string | null | undefined): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed) ? "--" : `${(parsed * 100).toFixed(4)}%`;
};

const compactCcy = (value: string | null | undefined): string => {
  const parsed = Number(value);
  if (value == null || value === "" || !Number.isFinite(parsed)) return "--";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 2 }).format(parsed);
};

const compactUsd = (value: string | null | undefined): string => {
  const parsed = Number(value);
  if (value == null || value === "" || !Number.isFinite(parsed)) return "--";
  return new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(parsed);
};

function changeClass(value: string | null | undefined): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed === 0) return "";
  return parsed > 0 ? "positive" : "negative";
}

function LineChart({
  points,
  height = 150,
  minDomain,
  maxDomain,
  ariaLabel,
}: {
  points: { label: string; value: number }[];
  height?: number;
  minDomain?: number;
  maxDomain?: number;
  ariaLabel: string;
}) {
  if (points.length < 2) return null;
  const width = 640;
  const values = points.map((point) => point.value);
  const min = minDomain ?? Math.min(...values);
  const max = maxDomain ?? Math.max(...values);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);
  const yFor = (value: number) => height - ((value - min) / span) * (height - 18) - 9;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${(index * stepX).toFixed(1)},${yFor(point.value).toFixed(1)}`)
    .join(" ");
  const area = `${path} L${width},${height - 4} L0,${height - 4} Z`;
  const first = points[0];
  const last = points[points.length - 1];

  return (
    <div className="macro-chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={ariaLabel}>
        <path d={area} className="macro-chart-area" />
        <path d={path} fill="none" strokeWidth={2.5} className="macro-chart-line" />
      </svg>
      <div className="macro-chart-axis">
        <span>{first.label}</span>
        <span>{last.label}</span>
      </div>
    </div>
  );
}

function DominanceBars({ btc, eth }: { btc: string | null | undefined; eth: string | null | undefined }) {
  const rows = [
    { label: "BTC", value: Number(btc), className: "btc" },
    { label: "ETH", value: Number(eth), className: "eth" },
  ].filter((row) => Number.isFinite(row.value));
  if (rows.length === 0) return null;
  return (
    <div className="dominance-bars">
      {rows.map((row) => (
        <div key={row.label}>
          <span>{row.label}</span>
          <div><i className={row.className} style={{ width: `${Math.max(2, Math.min(100, row.value))}%` }} /></div>
          <strong>{row.value.toFixed(2)}%</strong>
        </div>
      ))}
    </div>
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
  const fearGreedChart = fearGreedHistory.map((point) => ({ label: point.date, value: point.value }));
  const mvrv = data?.mvrv ?? null;
  const mvrvValue = mvrv?.value != null ? Number(mvrv.value) : null;
  const mvrvClassification = mvrv?.classification ?? null;
  const mvrvHistory: MvrvPoint[] = mvrv?.history ?? [];
  const mvrvChart = mvrvHistory
    .map((point) => ({ label: point.date, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value));
  const prices = data?.prices ?? [];
  const fundingEntries = data?.funding?.entries ?? [];
  const globalMarket = data?.global_market ?? null;

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>市場總覽</h2>
            <p>整合免費鏈上估值、情緒、全球市值與 OKX BTC/ETH 衍生品資料，先判斷大盤環境，再回到交易計畫。</p>
          </div>
          {data && <span className="badge info"><RefreshCw size={13} /> 每 60 秒更新</span>}
        </div>
      </section>

      {overview.error && <div className="error-state"><AlertTriangle size={17} />市場總覽 API 無法連線。</div>}
      {overview.isLoading && <div className="loading-state">正在取得市場總覽資料...</div>}

      {data && (
        <div className="macro-grid">
          <section className="panel macro-card macro-card-wide">
            <div className="panel-heading"><h2><Globe2 size={17} /> 全球加密市場</h2></div>
            {globalMarket?.unavailable_reason ? (
              <div className="empty-state"><AlertTriangle size={16} />{globalMarket.unavailable_reason}</div>
            ) : (
              <>
                <div className="metric-grid macro-price-grid">
                  <div><small>總市值</small><strong>{compactUsd(globalMarket?.total_market_cap_usd)}</strong><span className={changeClass(globalMarket?.market_cap_change_24h_pct)}>{percent(globalMarket?.market_cap_change_24h_pct)}</span></div>
                  <div><small>24h 成交量</small><strong>{compactUsd(globalMarket?.total_volume_usd)}</strong><span className={changeClass(globalMarket?.volume_change_24h_pct)}>{percent(globalMarket?.volume_change_24h_pct)}</span></div>
                  <div><small>活躍幣種</small><strong>{globalMarket?.active_cryptocurrencies?.toLocaleString("zh-TW") ?? "--"}</strong><span>{globalMarket?.markets ? `${globalMarket.markets} exchanges` : "markets --"}</span></div>
                </div>
                <DominanceBars btc={globalMarket?.btc_dominance_pct} eth={globalMarket?.eth_dominance_pct} />
              </>
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
                <LineChart points={fearGreedChart} minDomain={0} maxDomain={100} ariaLabel="Fear and Greed history" />
                <small className="macro-as-of">過去 {fearGreedHistory.length} 天情緒走勢</small>
              </>
            ) : (
              <div className="empty-state"><AlertTriangle size={16} />暫無資料</div>
            )}
          </section>

          <section className="panel macro-card macro-card-wide">
            <div className="panel-heading"><h2><Bitcoin size={17} /> MVRV Z-Score</h2></div>
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
                {mvrvChart.length > 1 && <LineChart points={mvrvChart} ariaLabel="MVRV Z-Score history" />}
                {mvrvClassification && <p className="macro-hint">{MVRV_CLASSIFICATION_HINT[mvrvClassification]}</p>}
                {mvrv?.as_of && <small className="macro-as-of">資料日期：{mvrv.as_of}</small>}
              </>
            ) : (
              <div className="empty-state"><AlertTriangle size={16} />暫無資料</div>
            )}
          </section>

          <section className="panel macro-card">
            <div className="panel-heading"><h2><TrendingUp size={17} /> BTC/ETH 即時價格</h2></div>
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

          <section className="panel macro-card macro-card-wide">
            <div className="panel-heading"><h2><Activity size={17} /> 資金費率與未平倉</h2></div>
            {data.funding.unavailable_reason ? (
              <div className="empty-state"><AlertTriangle size={16} />{data.funding.unavailable_reason}</div>
            ) : (
              <>
                {data.funding.weighted_average_funding_rate != null && (
                  <div className="macro-headline">
                    <strong className={changeClass(data.funding.weighted_average_funding_rate)}>
                      {fundingPct(data.funding.weighted_average_funding_rate)}
                    </strong>
                    <span>OI 加權平均資金費率</span>
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
