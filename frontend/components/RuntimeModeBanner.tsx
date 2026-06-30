"use client";

import useSWR from "swr";
import { AlertTriangle, FlaskConical, ShieldCheck, ShieldOff } from "lucide-react";

import { getEntryControl, getLivePreflight, getRiskLimits } from "@/lib/api";

type Mode = "dry" | "unarmed" | "armed" | "blocked" | "stale";

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
    title: "執行狀態已過期",
    detail: "後端未提供足夠新鮮的安全狀態，請勿假設目前可以安全送單。",
  },
};

function stale(timestamp?: string | null): boolean {
  if (!timestamp) return true;
  const age = Date.now() - new Date(timestamp).getTime();
  return !Number.isFinite(age) || age > 120_000;
}

export default function RuntimeModeBanner() {
  const preflight = useSWR("runtime-preflight", getLivePreflight, { refreshInterval: 10_000 });
  const risk = useSWR("risk-limits", getRiskLimits, { refreshInterval: 10_000 });
  const entries = useSWR("entry-control", getEntryControl, { refreshInterval: 10_000 });

  let mode: Mode = "stale";
  if (!preflight.error && preflight.data && !stale(preflight.data.checked_at)) {
    if (!preflight.data.passed || (preflight.data.execution_mode !== "dry_run" && !risk.data?.enabled)) {
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

  return (
    <section className={`mode-banner mode-${mode}`} aria-live="polite">
      <Icon size={24} aria-hidden="true" />
      <div className="mode-copy">
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
      <div className="mode-facts">
        <span>模式：{preflight.data?.execution_mode?.replace("_", " ") ?? "未知"}</span>
        <span>委託：{preflight.data?.armed ? "已武裝" : "已解除"}</span>
        <span>進場：{entries.data?.entries_enabled && entries.data.process_entry_enabled ? "已啟用" : "已停用"}</span>
      </div>
    </section>
  );
}
