"use client";

import useSWR from "swr";
import { Activity, Bitcoin, CircleDollarSign, WalletCards } from "lucide-react";

import RealMoneyGuide from "@/components/RealMoneyGuide";
import RuntimeModeBanner from "@/components/RuntimeModeBanner";
import { getAccountSnapshot, getBtcRegime } from "@/lib/api";

function currency(value: unknown): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "資料不足";
}

function text(value: unknown): string {
  return value === null || value === undefined || value === "" ? "資料不足" : String(value);
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
        {account.error ? <div className="error-state">帳戶 API 無法使用，請確認 FastAPI、認證與 CORS 設定。</div> : !account.data ? <div className="loading-state">正在讀取帳戶…</div> : <div className="dashboard-metrics">
          <article className="panel dashboard-metric"><CircleDollarSign size={22} /><small>總權益</small><strong>{currency(account.data.summary?.total_equity)} USDT</strong></article>
          <article className="panel dashboard-metric"><WalletCards size={22} /><small>可用權益</small><strong>{currency(account.data.summary?.available_equity)} USDT</strong></article>
          <article className="panel dashboard-metric"><Activity size={22} /><small>未實現損益</small><strong className={Number.isFinite(pnl) && pnl >= 0 ? "positive" : "negative"}>{currency(account.data.summary?.unrealized_pnl)} USDT</strong></article>
        </div>}
      </section>
      <section className="panel">
        <div className="panel-heading"><div><h2><Bitcoin size={21} /> BTC 市場狀態</h2><p>策略政策使用的即時市場分類與證據。</p></div>{regime.data?.updated_at && <span className="badge info">更新：{new Date(regime.data.updated_at).toLocaleTimeString("zh-TW")}</span>}</div>
        {regime.error ? <div className="error-state">BTC 市場狀態 API 無法使用；策略可能仍在等待市場資料。</div> : !regime.data ? <div className="loading-state">正在讀取市場狀態…</div> : <div className="metric-grid"><div><small>方向</small><strong>{text(regime.data.direction)}</strong></div><div><small>強度</small><strong>{text(regime.data.strength)}</strong></div><div><small>動能</small><strong>{text(regime.data.impulse)}</strong></div><div><small>BTC 價格</small><strong>{currency(regime.data.price)}</strong></div></div>}
      </section>
      <RealMoneyGuide />
    </div>
  );
}
