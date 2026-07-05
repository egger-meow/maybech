"use client";

import useSWR from "swr";
import { useRef, useState } from "react";
import { AlertTriangle, FlaskConical, ShieldCheck, ShieldOff } from "lucide-react";

import { ApiError, enableEntries, getEntryControl, getExecutionFillStatus, getLivePreflight, getRiskLimits, killEntries, listInstruments, listStrategies } from "@/lib/api";

type Mode = "simulation" | "demo" | "live_safe" | "live_armed" | "blocked" | "stale";

type DiagnosticCard = {
  endpoint: string;
  status: string;
  detail: string;
};

const modeCopy: Record<Mode, { title: string; detail: string }> = {
  simulation: {
    title: "Simulation",
    detail: "Signal／Risk／Strategy／Position 啟用；完全不連接交易所。",
  },
  demo: {
    title: "Demo",
    detail: "Signal／Risk／Strategy／Position 啟用；OKX Demo 委託已啟用。",
  },
  live_safe: {
    title: "Live Safe",
    detail: "正式交易所讀取與復原已啟用；所有委託停用。",
  },
  live_armed: {
    title: "Live Armed",
    detail: "真實委託已啟用；新進場仍須另外開啟進場閘門。",
  },
  blocked: {
    title: "安全檢查封鎖中",
    detail: "啟動檢查或風險設定不完整，交易操作必須維持停用。",
  },
  stale: {
    title: "無法確認執行狀態",
    detail: "啟動檢查 API 無法讀取，請勿假設目前可以安全送單。",
  },
};

function failure(error: unknown, missingLabel = "無法讀取"): string {
  if (error instanceof ApiError) return `${missingLabel}（HTTP ${error.status}）`;
  return error ? `${missingLabel}（無回應）` : "讀取中";
}

function formatTime(value: string | undefined) {
  if (!value) return "未提供時間";
  return new Date(value).toLocaleString("zh-TW");
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export default function RuntimeModeBanner() {
  const preflight = useSWR("runtime-preflight", getLivePreflight, { refreshInterval: 10_000 });
  const risk = useSWR("risk-limits", getRiskLimits, { refreshInterval: 10_000 });
  const entries = useSWR("entry-control", getEntryControl, { refreshInterval: 10_000 });
  const execution = useSWR("execution-fill-health", getExecutionFillStatus, { refreshInterval: 3_000 });
  const strategies = useSWR("strategies", listStrategies, { refreshInterval: 10_000 });
  const instruments = useSWR("instrument-metadata", listInstruments, { refreshInterval: 60_000 });
  const entryMutation = useRef(false);
  const [entryBusy, setEntryBusy] = useState<"enable" | "kill" | "">("");
  const [entryMessage, setEntryMessage] = useState("");

  const mutateEntries = async (action: "enable" | "kill") => {
    if (entryMutation.current) return;
    const prompt = action === "enable"
      ? "確認啟用新的策略進場？只有已通過 preflight、風險限制與策略檢查的委託才可送出。"
      : "確認立即停止所有新進場，並取消 Maybech 尚未成交的進場委託？減倉與保護性出場不受影響。";
    if (!confirm(prompt)) return;
    entryMutation.current = true;
    setEntryBusy(action);
    setEntryMessage("");
    try {
      const result = action === "enable"
        ? await enableEntries({ confirm: true })
        : await killEntries({ confirm: true });
      await entries.mutate(result, { revalidate: true });
      setEntryMessage(action === "enable"
        ? "新進場已啟用。"
        : `新進場已停止；要求取消 ${result.cancellations_requested} 筆，未解決 ${result.unresolved} 筆${(result.errors ?? []).length ? `；錯誤：${(result.errors ?? []).join("、")}` : ""}。`);
    } catch (error) {
      setEntryMessage(error instanceof Error ? error.message : "進場控制 API 無法使用。");
    } finally {
      entryMutation.current = false;
      setEntryBusy("");
    }
  };

  let mode: Mode = "stale";
  if (!preflight.error && preflight.data) {
    const executionBlocked = preflight.data.order_submission_enabled
      && (!execution.data?.healthy || Boolean(execution.error));
    if (!preflight.data.passed || executionBlocked || (preflight.data.order_submission_enabled && (!risk.data?.enabled || Boolean(risk.error)))) {
      mode = "blocked";
    } else {
      mode = preflight.data.execution_mode;
    }
  }

  const Icon = mode === "simulation" || mode === "demo" ? FlaskConical : mode === "live_armed" ? ShieldCheck : mode === "live_safe" ? ShieldOff : AlertTriangle;
  const copy = modeCopy[mode];
  const riskState = risk.data
    ? risk.data.enabled ? "已啟用" : "已建立但停用"
    : risk.error instanceof ApiError && risk.error.status === 404 ? "尚未設定（HTTP 404）" : failure(risk.error);
  const entryState = entries.data
    ? entries.data.entries_enabled && entries.data.process_entry_enabled ? "已啟用" : "已停用"
    : failure(entries.error);
  const allowedInstruments = risk.data?.allowed_instruments ?? [];
  const executionEvidence = execution.data
    ? `REST ${execution.data.caught_up ? "已追平" : "追補中"}；私有串流 ${execution.data.websocket_connected ? "已連線" : "未連線"}；重連 ${execution.data.websocket_reconnects ?? 0} 次；丟棄 ${execution.data.websocket_dropped_events ?? 0} 筆${execution.data.last_health_failure_at ? `；最近異常 ${formatTime(execution.data.last_health_failure_at)}` : ""}`
    : "持續檢查成交補抓、游標、私有串流、配置與保護狀態。";

  const diagnostics: DiagnosticCard[] = [
    {
      endpoint: "/runtime/preflight",
      status: preflight.data ? "正常" : failure(preflight.error),
      detail: preflight.data ? `啟動檢查 ${formatTime(preflight.data.checked_at)}` : "這是執行模式的主來源。",
    },
    {
      endpoint: "/execution/fills/status",
      status: execution.data
        ? execution.data.healthy ? "健康" : `封鎖（${execution.data.health_state}）`
        : failure(execution.error),
      detail: execution.data
        ? execution.data.health_reasons?.length
          ? `${execution.data.health_reasons.join("；")}。${executionEvidence}`
          : `持續監控正常；${executionEvidence}；更新 ${formatTime(execution.data.updated_at ?? undefined)}`
        : executionEvidence,
    },
    {
      endpoint: "/risk/limits",
      status: riskState,
      detail: risk.data ? `更新 ${formatTime(risk.data.updated_at)}` : "風險上限與實盤解鎖條件。",
    },
    {
      endpoint: "/risk/entries",
      status: entries.data ? entryState : failure(entries.error),
      detail: entries.data ? `更新 ${formatTime(entries.data.updated_at)}` : "進場閘門與策略武裝狀態。",
    },
  ];
  const requirements: { label: string; state: "ready" | "missing" | "unchecked"; detail: string }[] = [];
  if (!risk.data && risk.error instanceof ApiError && risk.error.status === 404) {
    requirements.push(
      { label: "單筆名目金額上限", state: "missing", detail: "缺少 max_order_notional_usd" },
      { label: "帳戶總曝險上限", state: "missing", detail: "缺少 max_total_exposure_usd" },
      { label: "最大槓桿", state: "missing", detail: "缺少 max_leverage" },
      { label: "帳戶允許商品", state: "missing", detail: "缺少 allowed_instruments" },
      { label: "風險上限啟用狀態", state: "missing", detail: "風險 envelope 尚未建立並啟用" },
    );
  } else if (risk.data) {
    requirements.push(
      { label: "單筆名目金額上限", state: "ready", detail: `${risk.data.max_order_notional_usd} USD` },
      { label: "帳戶總曝險上限", state: "ready", detail: `${risk.data.max_total_exposure_usd} USD` },
      { label: "最大槓桿", state: "ready", detail: `${risk.data.max_leverage}×` },
      { label: "帳戶允許商品", state: allowedInstruments.length ? "ready" : "missing", detail: allowedInstruments.length ? allowedInstruments.join("、") : "allowlist 為空，實盤進場會被封鎖" },
      { label: "風險上限啟用狀態", state: risk.data.enabled ? "ready" : "missing", detail: risk.data.enabled ? "已啟用" : "已建立但 enabled=false" },
    );
  } else {
    requirements.push(
      { label: "單筆名目金額上限", state: "unchecked", detail: "風險 API 尚未回應" },
      { label: "帳戶總曝險上限", state: "unchecked", detail: "風險 API 尚未回應" },
      { label: "最大槓桿", state: "unchecked", detail: "風險 API 尚未回應" },
      { label: "帳戶允許商品", state: "unchecked", detail: "風險 API 尚未回應" },
      { label: "風險上限啟用狀態", state: "unchecked", detail: "風險 API 尚未回應" },
    );
  }
  requirements.push({
    label: "OKX 商品 metadata",
    state: instruments.data?.items.length && !instruments.data.stale ? "ready" : "missing",
    detail: instruments.data?.items.length ? instruments.data.stale ? `商品快取已過期（${formatTime(instruments.data.refreshed_at)}）` : `${instruments.data.items.length} 個可交易 SWAP 已快取` : "商品快取缺失，策略與手動開倉不能選商品",
  });
  for (const strategy of strategies.data?.filter((item) => item.enabled) ?? []) {
    const metadata = object(strategy.metadata);
    const sizes = object(metadata.order_size_contracts);
    const slippage = Number(metadata.max_entry_slippage_pct);
    const closeConditions = object(strategy.default_rules).close_conditions;
    const side = metadata.position_side === "short" ? "short" : "long";
    const expectedStop = side === "long" ? "price_below" : "price_above";
    const hasStop = Array.isArray(closeConditions) && closeConditions.some((item) => {
      const rule = object(item); const expression = object(rule.expression);
      return rule.purpose === "stop_loss" && rule.enabled !== false && expression.type === expectedStop;
    });
    const missingSizes = (strategy.target_instruments ?? []).filter((item) => !(Number(sizes[item]) > 0));
    const outsideAllowlist = (strategy.target_instruments ?? []).filter((item) => !allowedInstruments.includes(item));
    requirements.push(
      { label: `${strategy.name}／帳戶商品邊界`, state: risk.data && !outsideAllowlist.length ? "ready" : "missing", detail: !risk.data ? "風險 envelope 尚未讀取" : outsideAllowlist.length ? `超出 allowlist：${outsideAllowlist.join("、")}` : "所有策略商品均在帳戶 allowlist" },
      { label: `${strategy.name}／委託口數`, state: Boolean(strategy.target_instruments?.length) && !missingSizes.length ? "ready" : "missing", detail: missingSizes.length ? `缺少：${missingSizes.join("、")}` : strategy.target_instruments?.length ? "每個商品已有 OKX 口數" : "沒有 target instrument" },
      { label: `${strategy.name}／最大滑價`, state: slippage > 0 && slippage <= .05 ? "ready" : "missing", detail: slippage > 0 && slippage <= .05 ? `${slippage * 100}%` : "max_entry_slippage_pct 必須大於 0 且不超過 5%" },
      { label: `${strategy.name}／保護停損`, state: hasStop ? "ready" : "missing", detail: hasStop ? `已有方向正確的 ${expectedStop}` : `缺少已啟用的 ${expectedStop} stop_loss` },
    );
  }
  requirements.push(
    { label: "OKX 衍生品帳戶模式", state: preflight.data?.account_level ? "ready" : "unchecked", detail: preflight.data?.account_level ? `acctLv=${preflight.data.account_level}` : "Simulation 不適用；Demo／Live 要求 acctLv 2、3 或 4" },
    { label: "OKX 部位模式", state: preflight.data?.position_mode === "net_mode" ? "ready" : "unchecked", detail: preflight.data?.position_mode || "Simulation 不適用；Demo／Live 必須是 net_mode" },
  );

  return (
    <section className={`mode-banner mode-${mode}`} aria-live="polite">
      <div className="mode-banner-head">
        <Icon size={24} aria-hidden="true" />
        <div className="mode-copy">
          <strong>{copy.title}</strong>
          <span>{copy.detail}</span>
        </div>
      </div>
      <div className="mode-facts">
        <span>模式：{preflight.data?.execution_mode?.replace("_", " ") ?? "未知"}</span>
        <span>憑證：{preflight.data?.credential_environment ?? "未知"}</span>
        <span>委託：{preflight.data ? preflight.data.armed ? "已武裝" : "已解除" : "未知"}</span>
        <span>進場：{entries.data ? entryState : "未知"}</span>
      </div>
      <div className="form-actions" aria-label="策略進場控制">
        <button type="button" className="btn btn-primary" disabled={Boolean(entryBusy) || Boolean(entries.data?.entries_enabled) || Boolean(preflight.data?.order_submission_enabled && !execution.data?.healthy)} onClick={() => mutateEntries("enable")}>{entryBusy === "enable" ? "啟用中…" : "啟用新進場"}</button>
        <button type="button" className="btn btn-danger" disabled={Boolean(entryBusy)} onClick={() => mutateEntries("kill")}>{entryBusy === "kill" ? "停止與取消中…" : "停止新進場（Kill）"}</button>
      </div>
      {entryMessage && <div className={entryMessage.includes("錯誤") || entryMessage.includes("無法") ? "error-state" : "mode-notice"}>{entryMessage}</div>}
      {mode === "simulation" && risk.error instanceof ApiError && risk.error.status === 404 && <div className="mode-notice">風險上限尚未建立；Simulation 可繼續使用，Demo 與 Live Armed 會被 preflight 封鎖。</div>}
      <details className="mode-requirements">
        <summary>實盤啟動條件逐項檢查</summary>
        <div className="mode-requirements-grid">
          {requirements.map((item) => <article key={item.label} className={`requirement-${item.state}`}><span>{item.state === "ready" ? "已具備" : item.state === "missing" ? "缺少" : "尚未驗證"}</span><strong>{item.label}</strong><small>{item.detail}</small></article>)}
        </div>
      </details>
      <details className="mode-diagnostics">
        <summary>
          <span>安全端點診斷</span>
          <span className="mode-diagnostics-hint">查看 API 狀態與最近更新時間</span>
        </summary>
        <div className="mode-diagnostics-grid">
          {diagnostics.map((item) => (
            <article className="mode-diagnostic-card" key={item.endpoint}>
              <code>{item.endpoint}</code>
              <strong>{item.status}</strong>
              <span>{item.detail}</span>
            </article>
          ))}
        </div>
      </details>
    </section>
  );
}
