"use client";

import useSWR from "swr";
import { AlertTriangle, FlaskConical, ShieldCheck, ShieldOff } from "lucide-react";

import { ApiError, getEntryControl, getLivePreflight, getRiskLimits, listInstruments, listStrategies } from "@/lib/api";

type Mode = "dry" | "unarmed" | "armed" | "blocked" | "stale";

type DiagnosticCard = {
  endpoint: string;
  status: string;
  detail: string;
};

const modeCopy: Record<Mode, { title: string; detail: string }> = {
  dry: {
    title: "模擬執行（Dry-run）",
    detail: "可查看訊號與模擬動作；系統不會送出任何真實委託。",
  },
  unarmed: {
    title: "實盤環境 · 尚未武裝",
    detail: "執行環境已連接交易所帳戶，但委託功能尚未武裝。",
  },
  armed: {
    title: "實盤環境 · 已武裝",
    detail: "減倉與平倉規則可能送單；新進場仍須另外開啟進場閘門。",
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
  const strategies = useSWR("strategies", listStrategies, { refreshInterval: 10_000 });
  const instruments = useSWR("instrument-metadata", listInstruments, { refreshInterval: 60_000 });

  let mode: Mode = "stale";
  if (!preflight.error && preflight.data) {
    if (!preflight.data.passed || (preflight.data.execution_mode !== "dry_run" && (!risk.data?.enabled || Boolean(risk.error)))) {
      mode = "blocked";
    } else if (preflight.data.execution_mode === "dry_run") {
      mode = "dry";
    } else if (preflight.data.armed) {
      mode = "armed";
    } else {
      mode = "unarmed";
    }
  }

  const Icon = mode === "dry" ? FlaskConical : mode === "armed" ? ShieldCheck : mode === "unarmed" ? ShieldOff : AlertTriangle;
  const copy = modeCopy[mode];
  const riskState = risk.data
    ? risk.data.enabled ? "已啟用" : "已建立但停用"
    : risk.error instanceof ApiError && risk.error.status === 404 ? "尚未設定（HTTP 404）" : failure(risk.error);
  const entryState = entries.data
    ? entries.data.entries_enabled && entries.data.process_entry_enabled ? "已啟用" : "已停用"
    : failure(entries.error);
  const allowedInstruments = risk.data?.allowed_instruments ?? [];

  const diagnostics: DiagnosticCard[] = [
    {
      endpoint: "/runtime/preflight",
      status: preflight.data ? "正常" : failure(preflight.error),
      detail: preflight.data ? `啟動檢查 ${formatTime(preflight.data.checked_at)}` : "這是執行模式的主來源。",
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
    { label: "OKX 衍生品帳戶模式", state: preflight.data?.account_level ? "ready" : "unchecked", detail: preflight.data?.account_level ? `acctLv=${preflight.data.account_level}` : "Dry-run 未驗證；實盤要求 acctLv 2、3 或 4" },
    { label: "OKX 部位模式", state: preflight.data?.position_mode === "net_mode" ? "ready" : "unchecked", detail: preflight.data?.position_mode || "Dry-run 未驗證；實盤必須是 net_mode" },
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
        <span>委託：{preflight.data ? preflight.data.armed ? "已武裝" : "已解除" : "未知"}</span>
        <span>進場：{entries.data ? entryState : "未知"}</span>
      </div>
      {mode === "dry" && risk.error instanceof ApiError && risk.error.status === 404 && <div className="mode-notice">風險上限尚未建立；明確缺少：單筆名目金額上限、帳戶總曝險上限、最大槓桿、允許商品及啟用狀態。Dry-run 可繼續使用，實盤啟動會被封鎖。</div>}
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
