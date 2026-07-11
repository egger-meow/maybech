"use client";

import { BarChart3, FlipHorizontal2, ShieldAlert } from "lucide-react";
import { Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";

import InstrumentSelector from "@/components/InstrumentSelector";
import KlineChart from "@/components/KlineChart";
import CenterCandleControls from "@/components/research/CenterCandleControls";
import ReplayToolbar from "@/components/research/ReplayToolbar";
import { buildReflection } from "@/lib/research/reflection";
import { selectActualFollowing } from "@/lib/research/candleWindow";
import { compareProjectionToActual } from "@/lib/research/comparison";
import { useReplayIndex } from "@/lib/research/replay";
import { getMarketCandles, listInstruments, type CandleResponse } from "@/lib/api";

const bars = ["1m", "5m", "15m", "1H", "4H", "1D"];
const BAR_MINUTES: Record<string, number> = { "1m": 1, "5m": 5, "15m": 15, "1H": 60, "4H": 240, "1D": 1440 };
const FETCH_LIMIT = 300;

function formatDuration(totalMinutes: number): string {
  if (totalMinutes < 60) return `${totalMinutes} 分鐘`;
  if (totalMinutes < 1440) return `${(totalMinutes / 60).toFixed(1)} 小時`;
  return `${(totalMinutes / 1440).toFixed(1)} 天`;
}

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

function useSessionNumberState(key: string, fallback: number) {
  const [value, setValue] = useState<number>(() => {
    const stored = readSessionValue(key, "");
    const parsed = Number(stored);
    return stored && Number.isFinite(parsed) ? parsed : fallback;
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(key, String(value));
    } catch {
      // Browser storage can be unavailable in private or restricted contexts.
    }
  }, [key, value]);
  return [value, setValue] as const;
}

// Isolated per instrument+bar (via the `key` prop from the parent) so switching symbol or
// timeframe naturally resets the reflection center/replay position instead of carrying a
// stale index across an unrelated dataset.
function ReflectionWorkspace({
  instrument,
  bar,
  candles,
  reflectionCount,
}: {
  instrument: string;
  bar: string;
  candles: CandleResponse[];
  reflectionCount: number;
}) {
  const latestIndex = Math.max(0, candles.length - 1);
  const minIndex = Math.min(reflectionCount, latestIndex);
  const [stepMs, setStepMs] = useState(700);
  const replay = useReplayIndex({ min: minIndex, max: latestIndex, initial: latestIndex, stepMs });

  // /market/candles always returns the most recent FETCH_LIMIT candles, so on every
  // background refresh the whole window can slide forward by however many new candles
  // formed. A raw array index would then silently point at a different candle than the
  // one the user picked. Re-resolve the pinned candle by timestamp whenever the
  // underlying array identity changes, so a chosen center stays pinned to the same real
  // candle across refreshes instead of drifting.
  //
  // Pins by rawIndex (the operator's actual last choice), not the reflection-count-clamped
  // index — otherwise a background refetch re-syncs through the clamped position and
  // permanently overwrites the operator's original pick with it, silently erasing the
  // "reflection count pushed the center forward" state on the very next refresh.
  const prevCandlesRef = useRef<CandleResponse[]>([]);
  const rawIndexRef = useRef(replay.rawIndex);
  useEffect(() => {
    rawIndexRef.current = replay.rawIndex;
  }, [replay.rawIndex]);
  useEffect(() => {
    const prevCandles = prevCandlesRef.current;
    prevCandlesRef.current = candles;
    if (candles.length === 0) return;
    if (prevCandles.length === 0) {
      replay.setIndex(candles.length - 1);
      return;
    }
    const prevTimestamp = prevCandles[rawIndexRef.current]?.timestamp;
    if (prevTimestamp == null) return;
    const found = candles.findIndex((candle) => candle.timestamp === prevTimestamp);
    replay.setIndex(found >= 0 ? found : candles.length - 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles]);

  const centerIndex = Math.min(latestIndex, Math.max(0, replay.index));
  const centerCandle = candles[centerIndex];
  // reflectionCount raising `minIndex` clamps `replay.index` upward without moving the
  // raw index the operator actually picked — this is that gap, i.e. "was the center
  // silently pushed forward to satisfy the reflection count".
  const centerPushedForward = replay.rawIndex < minIndex;

  const reflection = useMemo(
    () => buildReflection(candles, centerIndex, reflectionCount),
    [candles, centerIndex, reflectionCount],
  );
  const actualFollowing = useMemo(
    () => selectActualFollowing(candles, centerIndex, reflectionCount),
    [candles, centerIndex, reflectionCount],
  );
  const stats = useMemo(
    () => compareProjectionToActual(reflection.reflected, actualFollowing),
    [reflection.reflected, actualFollowing],
  );

  const klineChart = useMemo(
    () => ({
      inst_id: instrument,
      bar,
      candles,
      fetched_at: "",
      overlays: candles.length > 0 ? [{ kind: "current", price: candles[candles.length - 1].close, label: "目前價" }] : [],
      reflectionCandles: reflection.reflected,
      reflectionCenterTimestamp: reflection.centerTimestamp,
    }),
    [instrument, bar, candles, reflection],
  );

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2><FlipHorizontal2 size={20} /> 反射中心與回放</h2>
            <p>拖曳滑桿或使用回放，將反射中心移至任一根歷史 K 棒，觀察反射路徑與之後真實走勢的對照。</p>
          </div>
        </div>
        {centerPushedForward && (
          <div className="inline-warning">反射根數需要更多歷史 K 棒，反射中心已自動前移以確保足夠資料；可調低反射根數取回原本的中心位置。</div>
        )}
        <CenterCandleControls
          minIndex={minIndex}
          maxIndex={latestIndex}
          index={centerIndex}
          onChange={(index) => replay.setIndex(index)}
          label={centerCandle ? new Date(centerCandle.timestamp).toLocaleString("zh-TW") : "—"}
        />
        <ReplayToolbar
          playing={replay.playing}
          onPlay={replay.play}
          onPause={replay.pause}
          onStepBackward={replay.stepBackward}
          onStepForward={replay.stepForward}
          onReset={replay.reset}
          stepMs={stepMs}
          onStepMsChange={setStepMs}
        />
      </section>

      <section className="panel analysis-chart-panel">
        <div className="panel-heading">
          <div>
            <h2><BarChart3 size={20} /> K 線與二次反射</h2>
            <p>紫色虛線區塊為反射（非預測）；真實 K 線一律以一般樣式顯示，兩者可同時對照。</p>
          </div>
        </div>
        <KlineChart chart={klineChart} showLegend={false} ariaLabel={`${instrument} 亞當理論二次反射 K 線`} />
        <div className="chart-legend">
          <span><i style={{ background: "#64748b" }} />真實 K 線</span>
          <span><i style={{ border: "1.5px dashed #8b5cf6", background: "transparent", borderRadius: 2 }} />反射路徑（非預測）</span>
          <span><i style={{ background: "#8b5cf6" }} />反射中心</span>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>反射與實際走勢比對</h2>
            <p>僅在反射中心之後仍有真實 K 線時才能比對；比對根數受限於反射根數與目前可取得的未來資料。</p>
          </div>
        </div>
        {stats.comparedCount > 0 ? (
          <div className="risk-sizing-result">
            <div><small>比對根數</small><strong>{stats.comparedCount}</strong></div>
            <div><small>方向吻合率</small><strong>{((stats.directionMatchRate ?? 0) * 100).toFixed(0)}%</strong></div>
            <div><small>平均價格誤差</small><strong>{(stats.meanAbsPriceErrorPct ?? 0).toFixed(2)}%</strong></div>
          </div>
        ) : (
          <div className="empty-state">反射中心之後尚無真實 K 線可比對；將中心移至較早的歷史 K 棒即可回放比對。</div>
        )}
      </section>

      <div className="analysis-boundary">
        <ShieldAlert size={18} />
        <div>
          <strong>研究用途，非交易訊號</strong>
          <p>反射路徑僅為亞當理論二次反射之幾何鏡射示意（將歷史 K 棒對反射中心做點反射），並非價格預測，不能直接作為進出場依據。</p>
        </div>
      </div>
    </>
  );
}

export default function AdamTheoryPage() {
  return (
    <Suspense fallback={<div className="loading-state">正在載入…</div>}>
      <AdamTheoryContent />
    </Suspense>
  );
}

function AdamTheoryContent() {
  const searchParams = useSearchParams();
  const instrumentParam = searchParams.get("instrument")?.trim().toUpperCase() || "";
  const initialInstrument = instrumentParam || "BTC-USDT-SWAP";
  const [draft, setDraft] = useSessionTextState("maybech.adamTheory.instrument.draft", initialInstrument, Boolean(instrumentParam));
  const [instrument, setInstrument] = useSessionTextState("maybech.adamTheory.instrument.active", initialInstrument, Boolean(instrumentParam));
  const [bar, setBar] = useSessionChoiceState("maybech.adamTheory.bar", "15m", bars);
  const [reflectionCount, setReflectionCount] = useSessionNumberState("maybech.adamTheory.reflectionCount", 30);

  const catalog = useSWR("instrument-metadata", listInstruments);
  const candlesQuery = useSWR(
    ["adam-theory-candles", instrument, bar],
    () => getMarketCandles(instrument, { bar, limit: FETCH_LIMIT }),
    { refreshInterval: 30_000 },
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = draft.trim().toUpperCase();
    if (next) setInstrument(next);
  };

  const clampedCount = Math.min(100, Math.max(5, Math.round(reflectionCount) || 30));
  const candleList = candlesQuery.data?.candles ?? [];

  return (
    <>
      <section className="panel">
        <form onSubmit={submit} className="analysis-form">
          <div className="field">
            <span>OKX 商品</span>
            <InstrumentSelector
              instruments={catalog.data?.items ?? []}
              stale={catalog.data?.stale ?? true}
              value={draft ? [draft] : []}
              onChange={(next) => setDraft(next[0] ?? "")}
            />
          </div>
          <label className="field">
            <span>時間週期</span>
            <select value={bar} onChange={(event) => setBar(event.target.value)}>
              {bars.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="field">
            <span>反射根數</span>
            <input
              type="number"
              min={5}
              max={100}
              step={1}
              value={reflectionCount}
              onChange={(event) => setReflectionCount(Number(event.target.value))}
              onBlur={() => setReflectionCount(clampedCount)}
            />
            <small>≈ {formatDuration(clampedCount * (BAR_MINUTES[bar] ?? 15))}</small>
          </label>
          <button className="btn btn-primary" type="submit" disabled={!draft || !catalog.data || catalog.data.stale}>
            <FlipHorizontal2 size={16} /> 產生反射
          </button>
        </form>
      </section>

      {catalog.error && <div className="error-state">商品快取無法使用；市場分析不接受未驗證的自由輸入。</div>}
      {candlesQuery.error && <div className="error-state">K 線資料無法取得。</div>}
      {candlesQuery.isLoading && <div className="loading-state">正在取得 K 線…</div>}
      {!candlesQuery.isLoading && !candlesQuery.error && candleList.length === 0 && (
        <div className="empty-state">此模式或商品目前沒有可用的 K 線資料（模擬模式不連線至交易所行情）。</div>
      )}
      {candleList.length > 0 && (
        <ReflectionWorkspace
          key={`${instrument}-${bar}`}
          instrument={instrument}
          bar={bar}
          candles={candleList}
          reflectionCount={clampedCount}
        />
      )}
    </>
  );
}
