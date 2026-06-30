"use client";

import useSWR from "swr";
import { AlertTriangle, FlaskConical, ShieldCheck, ShieldOff } from "lucide-react";

import { ApiError, getEntryControl, getLivePreflight, getRiskLimits } from "@/lib/api";

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

export default function RuntimeModeBanner() {
  const preflight = useSWR("runtime-preflight", getLivePreflight, { refreshInterval: 10_000 });
  const risk = useSWR("risk-limits", getRiskLimits, { refreshInterval: 10_000 });
  const entries = useSWR("entry-control", getEntryControl, { refreshInterval: 10_000 });

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
      {mode === "dry" && risk.error instanceof ApiError && risk.error.status === 404 && <div className="mode-notice">風險上限尚未建立；Dry-run 可繼續使用，但實盤啟動會被安全檢查封鎖。</div>}
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
