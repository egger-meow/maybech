"use client";

import { useMemo, useState, type MouseEvent } from "react";
import {
  Activity,
  AlertTriangle,
  Bitcoin,
  Clock,
  Droplets,
  Gauge,
  Globe2,
  ShieldQuestion,
  Wifi,
  WifiOff,
} from "lucide-react";
import useSWR from "swr";

import {
  getMarketIntelligenceMetrics,
  getMarketIntelligenceProviderStatus,
  getMarketIntelligenceSeries,
  getMarketRegime,
  type MarketIntelligenceMetricResponse,
  type MarketRegimeAssessmentResponse,
} from "@/lib/api";
import { formatPrice } from "@/lib/price-format";

type MetricDisplay = { label: string; format: (value: number) => string };

const compactUsd = (value: number): string =>
  new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);

const ratePct = (value: number): string => `${(value * 100).toFixed(4)}%`;
const plainPct = (value: number): string => `${value.toFixed(2)}%`;
const signedPct = (value: number): string => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const zScore = (value: number): string => value.toFixed(2);
const mvrvRatio = (value: number): string => `${value.toFixed(2)}x`;
const index0to100 = (value: number): string => Math.round(value).toString();

const PRICE_OI_REGIME_LABELS: Record<number, string> = {
  0: "盤整 / 無明顯訊號",
  1: "價漲＋OI增（新多單）",
  2: "價漲＋OI減（空頭回補）",
  3: "價跌＋OI增（新空單）",
  4: "價跌＋OI減（多頭平倉）",
};
const priceOiRegime = (value: number): string => PRICE_OI_REGIME_LABELS[Math.round(value)] ?? "未知";
const daysCount = (value: number): string => `${Math.round(value)} 天`;

const METRIC_DISPLAY: Record<string, MetricDisplay> = {
  crypto_fear_greed: { label: "恐懼與貪婪指數", format: index0to100 },
  crypto_fear_greed_avg_7d: { label: "恐懼貪婪指數（7 天均值）", format: index0to100 },
  crypto_fear_greed_avg_30d: { label: "恐懼貪婪指數（30 天均值）", format: index0to100 },
  crypto_fear_greed_percentile: { label: "恐懼貪婪指數分位數（站內資料）", format: plainPct },
  days_since_fear_greed_extreme: { label: "距上次極端情緒天數", format: daysCount },
  btc_mvrv_z: { label: "BTC MVRV Z-Score", format: zScore },
  btc_mvrv: { label: "BTC MVRV 比值", format: mvrvRatio },
  global_market_cap_usd: { label: "全球總市值", format: compactUsd },
  global_volume_24h_usd: { label: "全球 24h 成交量", format: compactUsd },
  btc_dominance_pct: { label: "BTC 市占率", format: plainPct },
  eth_dominance_pct: { label: "ETH 市占率", format: plainPct },
  okx_btc_price_usd: { label: "OKX BTC 價格", format: (v) => formatPrice(v) },
  okx_eth_price_usd: { label: "OKX ETH 價格", format: (v) => formatPrice(v) },
  okx_btc_funding_rate: { label: "OKX BTC 資金費率", format: ratePct },
  okx_eth_funding_rate: { label: "OKX ETH 資金費率", format: ratePct },
  okx_btc_oi_usd: { label: "OKX BTC 未平倉", format: compactUsd },
  okx_eth_oi_usd: { label: "OKX ETH 未平倉", format: compactUsd },
  okx_oi_weighted_funding: { label: "OI 加權平均資金費率", format: ratePct },
  okx_funding_annualized: { label: "資金費率（年化）", format: ratePct },
  okx_price_oi_regime: { label: "價格／未平倉部位型態", format: priceOiRegime },
  btc_mvrv_percentile: { label: "BTC MVRV 分位數（站內資料）", format: plainPct },
  stablecoin_total_mcap_usd: { label: "穩定幣總市值", format: compactUsd },
  stablecoin_mcap_change_7d_pct: { label: "穩定幣市值變化（7 天）", format: signedPct },
  stablecoin_mcap_change_30d_pct: { label: "穩定幣市值變化（30 天）", format: signedPct },
};

type Pillar = { id: string; label: string; icon: typeof Globe2; metricIds: string[]; wide: boolean };

const PILLARS: Pillar[] = [
  {
    id: "price_breadth",
    label: "價格與市場結構",
    icon: Globe2,
    metricIds: [
      "global_market_cap_usd",
      "global_volume_24h_usd",
      "btc_dominance_pct",
      "eth_dominance_pct",
      "okx_btc_price_usd",
      "okx_eth_price_usd",
    ],
    wide: true,
  },
  {
    id: "derivatives",
    label: "衍生品與槓桿壓力",
    icon: Activity,
    metricIds: [
      "okx_oi_weighted_funding",
      "okx_btc_funding_rate",
      "okx_eth_funding_rate",
      "okx_btc_oi_usd",
      "okx_eth_oi_usd",
      "okx_funding_annualized",
      "okx_price_oi_regime",
    ],
    wide: true,
  },
  {
    id: "valuation",
    label: "鏈上估值與週期",
    icon: Bitcoin,
    metricIds: ["btc_mvrv_z", "btc_mvrv", "btc_mvrv_percentile"],
    wide: false,
  },
  {
    id: "liquidity",
    label: "流動性與資金量能",
    icon: Droplets,
    metricIds: ["stablecoin_total_mcap_usd", "stablecoin_mcap_change_7d_pct", "stablecoin_mcap_change_30d_pct"],
    wide: false,
  },
  {
    id: "sentiment",
    label: "市場情緒與極端值",
    icon: Gauge,
    metricIds: [
      "crypto_fear_greed",
      "crypto_fear_greed_avg_7d",
      "crypto_fear_greed_avg_30d",
      "crypto_fear_greed_percentile",
      "days_since_fear_greed_extreme",
    ],
    wide: false,
  },
  { id: "holder_behavior", label: "持有者行為", icon: ShieldQuestion, metricIds: [], wide: false },
];

const FRESHNESS_LABEL: Record<string, { label: string; cls: string }> = {
  fresh: { label: "即時", cls: "success" },
  stale: { label: "稍舊", cls: "warning" },
  very_stale: { label: "過舊", cls: "danger" },
  unavailable: { label: "無資料", cls: "muted" },
};

function FreshnessBadge({ freshness }: { freshness: string | null | undefined }) {
  const entry = FRESHNESS_LABEL[freshness ?? "unavailable"] ?? FRESHNESS_LABEL.unavailable;
  return (
    <span className={`badge ${entry.cls}`}>
      <Clock size={11} /> {entry.label}
    </span>
  );
}

const REGIME_STATE_LABEL: Record<string, { label: string; cls: string }> = {
  supportive: { label: "偏正向", cls: "success" },
  neutral: { label: "中性", cls: "info" },
  cautious: { label: "留意", cls: "warning" },
  stressed: { label: "壓力偏高", cls: "danger" },
  unavailable: { label: "尚無評估", cls: "muted" },
};

function RegimeBadge({ assessment }: { assessment: MarketRegimeAssessmentResponse | undefined }) {
  const entry = REGIME_STATE_LABEL[assessment?.state ?? "unavailable"] ?? REGIME_STATE_LABEL.unavailable;
  return (
    <span className={`badge ${entry.cls}`}>
      {entry.label}
      {assessment && assessment.state !== "unavailable" ? ` · 信心 ${Math.round(assessment.confidence * 100)}%` : ""}
    </span>
  );
}

type ChartPoint = { value: number; shortLabel: string; fullLabel: string };

function LineChart({
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

function shortDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("zh-TW", { month: "2-digit", day: "2-digit" });
}

function fullDateTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString("zh-TW");
}

const TIMEFRAME_OPTIONS: { key: string; label: string; days: number | null }[] = [
  { key: "7d", label: "7 天", days: 7 },
  { key: "30d", label: "30 天", days: 30 },
  { key: "90d", label: "90 天", days: 90 },
  { key: "all", label: "全部", days: null },
];

function PillarSection({
  pillar,
  metricsById,
  regime,
  startIso,
}: {
  pillar: Pillar;
  metricsById: Record<string, MarketIntelligenceMetricResponse>;
  regime: MarketRegimeAssessmentResponse | undefined;
  startIso: string | undefined;
}) {
  const chartMetricId = pillar.metricIds[0] ?? null;
  const series = useSWR(
    chartMetricId ? ["market-intelligence-series", chartMetricId, startIso ?? "all"] : null,
    () => getMarketIntelligenceSeries(chartMetricId as string, { start: startIso, limit: 2000 }),
  );
  const chartDisplay = chartMetricId ? METRIC_DISPLAY[chartMetricId] : null;
  const chartPoints: ChartPoint[] = (series.data?.points ?? []).map((point) => ({
    value: point.value,
    shortLabel: shortDate(point.observed_at),
    fullLabel: fullDateTime(point.observed_at),
  }));
  const chartMetric = chartMetricId ? metricsById[chartMetricId] : null;

  return (
    <section className={`panel macro-card${pillar.wide ? " macro-card-wide" : ""}`}>
      <div className="panel-heading">
        <h2>
          <pillar.icon size={17} /> {pillar.label}
        </h2>
        <RegimeBadge assessment={regime} />
      </div>
      {regime && (
        <p className="macro-hint macro-regime-summary">{regime.summary}</p>
      )}

      {pillar.metricIds.length === 0 ? (
        <div className="empty-state">
          <AlertTriangle size={16} />
          尚無可用、有文件依據的免費資料來源；此面向留待後續階段補上，不以推測值填充。
        </div>
      ) : (
        <>
          <div className="metric-grid pillar-metric-grid">
            {pillar.metricIds.map((metricId) => {
              const metric = metricsById[metricId];
              const display = METRIC_DISPLAY[metricId];
              if (!metric || !display) return null;
              return (
                <div key={metricId}>
                  <small>{display.label}</small>
                  <strong>{metric.value != null ? display.format(metric.value) : "--"}</strong>
                  <FreshnessBadge freshness={metric.freshness} />
                </div>
              );
            })}
          </div>

          {chartMetricId && chartDisplay ? (
            chartPoints.length > 1 ? (
              <LineChart
                points={chartPoints}
                ariaLabel={`${chartDisplay.label} history`}
                valueFormat={chartDisplay.format}
              />
            ) : (
              <p className="macro-hint">尚未累積足夠歷史資料以繪製圖表，資料會隨時間持續累積。</p>
            )
          ) : null}

          {chartMetric && (
            <small className="macro-as-of">
              來源：{chartMetric.source_provider ?? "--"}
              {chartMetric.scope ? `（範圍：${chartMetric.scope}）` : ""}
              {chartMetric.caveats ? ` · ${chartMetric.caveats}` : ""}
            </small>
          )}
        </>
      )}
    </section>
  );
}

function startIsoForDays(days: number | null): string | undefined {
  // Only ever called from a useState lazy initializer or an event handler,
  // never during render — Date.now() there is fine, mid-render it isn't.
  return days != null ? new Date(Date.now() - days * 86_400_000).toISOString() : undefined;
}

export default function AnalysisOverviewPage() {
  const [timeframeKey, setTimeframeKey] = useState("30d");
  const [startIso, setStartIso] = useState<string | undefined>(() => startIsoForDays(30));

  const handleTimeframeChange = (key: string) => {
    setTimeframeKey(key);
    const days = TIMEFRAME_OPTIONS.find((option) => option.key === key)?.days ?? null;
    setStartIso(startIsoForDays(days));
  };

  const metricsQuery = useSWR("market-intelligence-metrics", getMarketIntelligenceMetrics, { refreshInterval: 60_000 });
  const providersQuery = useSWR("market-intelligence-providers", getMarketIntelligenceProviderStatus, {
    refreshInterval: 60_000,
  });
  const regimeQuery = useSWR("market-intelligence-regime", () => getMarketRegime(), { refreshInterval: 60_000 });

  const metrics = metricsQuery.data?.metrics ?? [];
  const metricsById = useMemo(() => {
    const list = metricsQuery.data?.metrics ?? [];
    return Object.fromEntries(list.map((metric) => [metric.metric_id, metric]));
  }, [metricsQuery.data]);
  const regimeByPillar = useMemo(() => {
    const list = regimeQuery.data?.pillars ?? [];
    return Object.fromEntries(list.map((assessment) => [assessment.pillar, assessment]));
  }, [regimeQuery.data]);
  const providers = providersQuery.data?.providers ?? [];
  const healthyProviders = providers.filter((provider) => provider.enabled && provider.consecutive_failures === 0).length;
  const latestUpdate = metrics.reduce<string | null>((latest, metric) => {
    if (!metric.observed_at) return latest;
    return !latest || metric.observed_at > latest ? metric.observed_at : latest;
  }, null);

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>市場總覽</h2>
            <p>
              依證據面向彙整免費鏈上估值、市場情緒、全球市值、OKX
              衍生品與穩定幣資料，協助判斷市場環境。本頁提供的是證據與脈絡，並非交易訊號、下單建議，也不會自動觸發任何倉位操作。
            </p>
          </div>
          <div className="segmented" role="group" aria-label="時間範圍">
            {TIMEFRAME_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                className={option.key === timeframeKey ? "selected" : ""}
                onClick={() => handleTimeframeChange(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="macro-status-strip">
          {providersQuery.data && (
            <span className={`badge ${healthyProviders === providers.length ? "success" : "warning"}`}>
              {healthyProviders === providers.length ? <Wifi size={13} /> : <WifiOff size={13} />}
              {healthyProviders}/{providers.length} 資料來源正常
            </span>
          )}
          {latestUpdate && (
            <span className="macro-as-of">
              <Clock size={12} /> 最新資料時間：{fullDateTime(latestUpdate)}
            </span>
          )}
          <span className="macro-as-of">
            更新頻率依來源而異：OKX 約 1 分鐘、CoinGecko／穩定幣約 5–30 分鐘、恐懼貪婪指數與 MVRV Z-Score 約每日一次。
          </span>
        </div>
      </section>

      {metricsQuery.error && (
        <div className="error-state">
          <AlertTriangle size={17} />
          市場總覽 API 無法連線。
        </div>
      )}
      {metricsQuery.isLoading && <div className="loading-state">正在取得市場總覽資料...</div>}

      {metrics.length > 0 && (
        <div className="macro-grid">
          {PILLARS.map((pillar) => (
            <PillarSection
              key={pillar.id}
              pillar={pillar}
              metricsById={metricsById}
              regime={regimeByPillar[pillar.id]}
              startIso={startIso}
            />
          ))}
        </div>
      )}
    </>
  );
}
