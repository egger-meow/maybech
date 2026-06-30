"use client";

import useSWR from "swr";
import { Activity, Bitcoin, CircleDollarSign, WalletCards } from "lucide-react";

import RealMoneyGuide from "@/components/RealMoneyGuide";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import { ApiError, getAccountSnapshot, getBtcRegime } from "@/lib/api";

function currency(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : null;
}

const marketLabels: Record<string, string> = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
  normal: "一般",
  strong: "強",
  weak: "弱",
  none: "無明顯動能",
  up: "向上",
  down: "向下",
};

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "資料不足";
  const raw = String(value);
  return marketLabels[raw.toLowerCase()] ?? raw;
}

function apiFailure(path: string, error: unknown): string {
  if (error instanceof ApiError) return `${path} 回傳 HTTP ${error.status}，請查看瀏覽器 Network 與後端日誌。`;
  return `${path} 無法連線，請確認 FastAPI、認證與 CORS 設定。`;
}

function Amount({ value, unit = "" }: { value: unknown; unit?: string }) {
  const formatted = currency(value);
  return <>{formatted ? `${formatted}${unit ? ` ${unit}` : ""}` : "資料不足"}</>;
}

export default function DashboardPage() {
  const account = useSWR("account-snapshot", getAccountSnapshot, { refreshInterval: 5000 });
  const regime = useSWR("btc-regime", getBtcRegime, { refreshInterval: 5000 });
  const pnl = Number(account.data?.summary?.unrealized_pnl);
  return (
    <div className="page-stack">
      <header className="page-header"><div><h1>交易工作台總覽</h1><p>先確認執行模式與資料新鮮度，再進入策略或邏輯部位管理。</p></div></header>
      <RuntimeModeBanner />
      <section>
        <div className="panel-heading"><div><h2>帳戶快照</h2><p>來自後端 Account Service；缺少資料時不會以零假裝正常。</p></div></div>
        {account.error ? <div className="error-state">{apiFailure("/account/snapshot", account.error)}</div> : !account.data ? <div className="loading-state">正在讀取帳戶…</div> : <div className="dashboard-metrics">
          <article className="panel dashboard-metric"><CircleDollarSign size={22} /><small>總權益</small><strong><Amount value={account.data.summary?.total_equity} unit="USDT" /></strong></article>
          <article className="panel dashboard-metric"><WalletCards size={22} /><small>可用權益</small><strong><Amount value={account.data.summary?.available_equity} unit="USDT" /></strong></article>
          <article className="panel dashboard-metric"><Activity size={22} /><small>未實現損益</small><strong className={Number.isFinite(pnl) ? pnl >= 0 ? "positive" : "negative" : ""}><Amount value={account.data.summary?.unrealized_pnl} unit="USDT" /></strong></article>
        </div>}
      </section>
      <section className="panel">
        <div className="panel-heading"><div><h2><Bitcoin size={21} /> BTC 市場狀態</h2><p>策略政策使用的即時市場分類與證據。</p></div>{regime.data?.updated_at && <span className="badge info">更新：{new Date(regime.data.updated_at).toLocaleTimeString("zh-TW")}</span>}</div>
        {regime.error ? <div className="error-state">{apiFailure("/market/btc-regime", regime.error)}</div> : !regime.data ? <div className="loading-state">正在讀取市場狀態…</div> : <div className="metric-grid"><div><small>方向</small><strong>{text(regime.data.direction)}</strong></div><div><small>強度</small><strong>{text(regime.data.strength)}</strong></div><div><small>動能</small><strong>{text(regime.data.impulse)}</strong></div><div><small>BTC 價格</small><strong><Amount value={regime.data.price} /></strong></div></div>}
      </section>
      <RealMoneyGuide />
    </div>
  );
}
