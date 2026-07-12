"use client";

import { AlertTriangle, BarChart3, Clock } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";

import InstrumentSelector from "@/components/InstrumentSelector";
import LineChart, { fullDateTime, shortDate, type ChartPoint } from "@/components/LineChart";
import { getOpenInterestHistory, listInstruments } from "@/lib/api";

const PERIODS: { key: string; label: string }[] = [
  { key: "5m", label: "5 分鐘" },
  { key: "1H", label: "1 小時" },
  { key: "1D", label: "1 天" },
];
const HISTORY_LIMIT = 200;

const compactUsd = (value: number): string =>
  new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);

const compactNumber = (value: number): string =>
  new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 2 }).format(value);

function readSessionValue(key: string, fallback: string, forceFallback = false) {
  if (forceFallback || typeof window === "undefined") return fallback;
  try {
    return sessionStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function useSessionTextState(key: string, fallback: string, forceFallback = false) {
  const [value, setValue] = useState(() => readSessionValue(key, fallback, forceFallback));
  useEffect(() => {
    try {
      sessionStorage.setItem(key, value);
    } catch {
      // Browser storage can be unavailable in private or restricted contexts.
    }
  }, [key, value]);
  return [value, setValue] as const;
}

function useSessionChoiceState<T extends string>(key: string, fallback: T, choices: readonly T[]) {
  const [value, setValue] = useState<T>(() => {
    const stored = readSessionValue(key, fallback);
    return choices.includes(stored as T) ? (stored as T) : fallback;
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(key, value);
    } catch {
      // Browser storage can be unavailable in private or restricted contexts.
    }
  }, [key, value]);
  return [value, setValue] as const;
}

export default function OpenInterestPage() {
  return (
    <Suspense fallback={<div className="loading-state">正在載入…</div>}>
      <OpenInterestContent />
    </Suspense>
  );
}

function OpenInterestContent() {
  const searchParams = useSearchParams();
  const instrumentParam = searchParams.get("instrument")?.trim().toUpperCase() || "";
  const initialInstrument = instrumentParam || "BTC-USDT-SWAP";
  const [instrument, setInstrument] = useSessionTextState(
    "maybech.openInterest.instrument",
    initialInstrument,
    Boolean(instrumentParam),
  );
  const [period, setPeriod] = useSessionChoiceState(
    "maybech.openInterest.period",
    "1H",
    PERIODS.map((p) => p.key),
  );

  const catalog = useSWR("instrument-metadata", listInstruments);
  const historyQuery = useSWR(
    ["open-interest-history", instrument, period],
    () => getOpenInterestHistory(instrument, { period, limit: HISTORY_LIMIT }),
    { refreshInterval: 60_000 },
  );

  const points = historyQuery.data?.points ?? [];
  const latest = points[points.length - 1];
  const chartPoints: ChartPoint[] = points.map((point) => ({
    value: point.oi_usd,
    shortLabel: shortDate(point.observed_at),
    fullLabel: fullDateTime(point.observed_at),
  }));

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>未平倉走勢</h2>
            <p>
              直接呼叫 OKX 各永續合約的未平倉歷史資料，可任選商品查看。本頁提供的是市場資料脈絡，並非交易訊號、下單建議，也不會自動觸發任何倉位操作。
            </p>
          </div>
          <div className="segmented" role="group" aria-label="時間週期">
            {PERIODS.map((option) => (
              <button
                key={option.key}
                type="button"
                className={option.key === period ? "selected" : ""}
                onClick={() => setPeriod(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <form
          className="analysis-form"
          onSubmit={(event) => event.preventDefault()}
        >
          <div className="field">
            <span>OKX 商品</span>
            <InstrumentSelector
              instruments={catalog.data?.items ?? []}
              stale={catalog.data?.stale ?? true}
              value={instrument ? [instrument] : []}
              onChange={(next) => setInstrument(next[0] ?? "")}
            />
          </div>
        </form>
      </section>

      {catalog.error && <div className="error-state">商品快取無法使用；未平倉走勢不接受未驗證的自由輸入。</div>}
      {historyQuery.error && <div className="error-state">未平倉歷史資料無法取得。</div>}
      {historyQuery.isLoading && <div className="loading-state">正在取得未平倉歷史資料…</div>}

      {historyQuery.data?.unavailable_reason && (
        <div className="empty-state">
          <AlertTriangle size={16} />
          {historyQuery.data.unavailable_reason}
        </div>
      )}

      {!historyQuery.isLoading && !historyQuery.data?.unavailable_reason && (
        <section className="panel macro-card">
          <div className="panel-heading">
            <h2>
              <BarChart3 size={17} /> {instrument}（{PERIODS.find((p) => p.key === period)?.label}）
            </h2>
          </div>

          {latest ? (
            <>
              <div className="metric-grid pillar-metric-grid">
                <div>
                  <small>最新未平倉量（USD）</small>
                  <strong>{compactUsd(latest.oi_usd)}</strong>
                </div>
                <div>
                  <small>最新未平倉量（結算貨幣）</small>
                  <strong>{compactNumber(latest.oi_ccy)}</strong>
                </div>
                <div>
                  <small>最新未平倉張數</small>
                  <strong>{compactNumber(latest.oi_contracts)}</strong>
                </div>
              </div>

              {chartPoints.length > 1 ? (
                <LineChart
                  points={chartPoints}
                  ariaLabel={`${instrument} open interest history`}
                  valueFormat={compactUsd}
                />
              ) : (
                <p className="macro-hint">目前週期下資料點不足以繪製圖表，可嘗試切換時間週期。</p>
              )}

              <small className="macro-as-of">
                <Clock size={12} /> 最新資料時間：{fullDateTime(latest.observed_at)}
              </small>
              <small className="macro-as-of">
                來源：OKX open-interest-history（範圍：{instrument}） · OKX 官方歷史資料，非 Maybech 自行累積；僅供市場脈絡參考，非交易訊號。
              </small>
            </>
          ) : (
            <div className="empty-state">此商品目前沒有可用的未平倉歷史資料。</div>
          )}
        </section>
      )}
    </>
  );
}
