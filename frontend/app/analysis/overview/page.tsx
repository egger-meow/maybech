"use client";

import { AlertTriangle, ArrowDown, ArrowUp, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";

import { getMarketOverview, type MarketOverviewResponse } from "@/lib/api";

type OverviewRow = NonNullable<MarketOverviewResponse["items"]>[number];
type SortKey = "volume" | "change" | "funding";

const numberFormat = (value: string | null | undefined, digits = 2): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed)
    ? "—"
    : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(parsed);
};

const compactUsd = (value: string | null | undefined): string => {
  const parsed = Number(value);
  if (value == null || value === "" || !Number.isFinite(parsed)) return "—";
  return new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 2 }).format(parsed);
};

const fundingPct = (value: string | null | undefined): string => {
  const parsed = Number(value);
  return value == null || value === "" || !Number.isFinite(parsed) ? "—" : `${(parsed * 100).toFixed(4)}%`;
};

const sortValue = (row: OverviewRow, key: SortKey): number => {
  if (key === "change") return Number(row.change_24h_pct ?? 0) || 0;
  if (key === "funding") return Number(row.funding_rate ?? 0) || 0;
  return Number(row.vol_24h_quote_ccy ?? 0) || 0;
};

function SortHeader({ label, sortKey, active, direction, onSort }: { label: string; sortKey: SortKey; active: boolean; direction: "asc" | "desc"; onSort: (key: SortKey) => void }) {
  return <th><button type="button" className="sort-header" onClick={() => onSort(sortKey)}>{label}{active && (direction === "desc" ? <ArrowDown size={13} /> : <ArrowUp size={13} />)}</button></th>;
}

export default function AnalysisOverviewPage() {
  const router = useRouter();
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("volume");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const overview = useSWR("market-overview", () => getMarketOverview({ instType: "SWAP" }), { refreshInterval: 15_000 });

  const rows = useMemo(() => {
    const needle = filter.trim().toUpperCase();
    const matched = (overview.data?.items ?? []).filter((row) => !needle || row.inst_id.includes(needle));
    return [...matched].sort((a, b) => (sortValue(a, sortKey) - sortValue(b, sortKey)) * (sortDirection === "desc" ? -1 : 1));
  }, [overview.data?.items, filter, sortKey, sortDirection]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) { setSortDirection(sortDirection === "desc" ? "asc" : "desc"); return; }
    setSortKey(key);
    setSortDirection("desc");
  };

  const openResearch = (instId: string) => router.push(`/analysis/research?instrument=${encodeURIComponent(instId)}`);

  return <>
    <section className="panel"><div className="panel-heading"><div><h2>OKX 永續全商品總覽</h2><p>依 24 小時成交額排序；點選商品可直接切換到支撐壓力研究並帶入該商品。</p></div>{overview.data && !overview.data.unavailable_reason && <span className="badge info"><RefreshCw size={13} /> 每 15 秒自動更新</span>}</div>
      <div className="field overview-filter"><Search size={16} /><input type="text" placeholder="篩選商品，如 BTC" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
    </section>
    {overview.error && <div className="error-state"><AlertTriangle size={17} />市場總覽 API 無法連線。</div>}
    {overview.isLoading && <div className="loading-state">正在取得全商品行情…</div>}
    {overview.data?.unavailable_reason && <div className="empty-state"><AlertTriangle size={16} />{overview.data.unavailable_reason}</div>}
    {overview.data && !overview.data.unavailable_reason && <section className="panel overview-table-panel">
      <div className="overview-table-scroll">
        <table className="overview-table">
          <thead><tr>
            <th>商品</th>
            <th>最新價</th>
            <SortHeader label="24h 漲跌" sortKey="change" active={sortKey === "change"} direction={sortDirection} onSort={onSort} />
            <th>24h 高</th>
            <th>24h 低</th>
            <SortHeader label="24h 成交額 (USDT)" sortKey="volume" active={sortKey === "volume"} direction={sortDirection} onSort={onSort} />
            <SortHeader label="資金費率" sortKey="funding" active={sortKey === "funding"} direction={sortDirection} onSort={onSort} />
          </tr></thead>
          <tbody>
            {rows.map((row) => {
              const change = Number(row.change_24h_pct);
              const changeClass = !Number.isFinite(change) ? "" : change > 0 ? "positive" : change < 0 ? "negative" : "";
              return <tr key={row.inst_id} onClick={() => openResearch(row.inst_id)} className="overview-row">
                <td><strong>{row.inst_id}</strong></td>
                <td>{numberFormat(row.last_price, row.price_precision ?? 2)}</td>
                <td className={changeClass}>{Number.isFinite(change) ? `${change > 0 ? "+" : ""}${change.toFixed(2)}%` : "—"}</td>
                <td>{numberFormat(row.high_24h, row.price_precision ?? 2)}</td>
                <td>{numberFormat(row.low_24h, row.price_precision ?? 2)}</td>
                <td>{compactUsd(row.vol_24h_quote_ccy)}</td>
                <td>{fundingPct(row.funding_rate)}</td>
              </tr>;
            })}
            {!rows.length && <tr><td colSpan={7} className="empty-state">沒有符合篩選條件的商品。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>}
  </>;
}
