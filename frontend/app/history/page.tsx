"use client";

import useSWR from "swr";
import { listTradeHistory } from "@/lib/api";

function formatNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 8 });
}

export default function History() {
  const { data: history, error } = useSWR(
    "trade-history",
    () => listTradeHistory(100),
    { refreshInterval: 10000 },
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <header>
        <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>交易紀錄</h1>
        <p style={{ color: "var(--text-muted)" }}>已確認成交的損益、費用與計算證據。</p>
      </header>

      <div className="glass-panel" style={{ padding: "1.5rem", overflowX: "auto" }}>
        {error ? (
          <div style={{ padding: "1rem", color: "var(--accent-danger)" }}>無法載入交易紀錄。</div>
        ) : history ? (
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th style={{ padding: "1rem 0.5rem" }}>時間</th>
                <th style={{ padding: "1rem 0.5rem" }}>商品</th>
                <th style={{ padding: "1rem 0.5rem" }}>方向</th>
                <th style={{ padding: "1rem 0.5rem" }}>原因</th>
                <th style={{ padding: "1rem 0.5rem", textAlign: "right" }}>已實現損益</th>
              </tr>
            </thead>
            <tbody>
              {history.length > 0 ? history.map((trade) => {
                const pnl = trade.pnl ?? 0;
                const side = trade.side.toLowerCase();
                const currency = trade.pnl_currency ?? "";
                return (
                  <tr key={trade.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <td style={{ padding: "1rem 0.5rem", fontSize: "0.9rem" }}>
                      <div>{trade.exit_time ? new Date(trade.exit_time).toLocaleString("zh-TW") : "尚未結束"}</div>
                      <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", marginTop: "0.25rem" }}>
                        <div>識別碼：{trade.short_id || trade.id}</div>
                        {trade.correlation_id ? <div>關聯 ID：{trade.correlation_id}</div> : null}
                        <div>入場：{trade.entry_time ? new Date(trade.entry_time).toLocaleString("zh-TW") : "—"}</div>
                        <div>出場：{trade.exit_time ? new Date(trade.exit_time).toLocaleString("zh-TW") : "尚未結束"}</div>
                        <div>策略：{trade.strategy_id || "手動"}</div>
                      </div>
                    </td>
                    <td style={{ padding: "1rem 0.5rem", fontWeight: 600 }}>{trade.inst_id}</td>
                    <td style={{ padding: "1rem 0.5rem" }}>
                      <span className={`badge ${side === "long" || side === "buy" ? "success" : "danger"}`}>{trade.side}</span>
                    </td>
                    <td style={{ padding: "1rem 0.5rem", color: "var(--text-muted)" }}>{trade.exit_reason}</td>
                    <td style={{ padding: "1rem 0.5rem", textAlign: "right", color: pnl >= 0 ? "var(--accent-success)" : "var(--accent-danger)" }}>
                      <strong>{pnl >= 0 ? "+" : ""}{formatNumber(pnl)} {currency}</strong>
                      <div style={{ fontSize: "0.8rem" }}>({formatNumber(trade.pnl_pct)}%)</div>
                      {trade.pnl_reliable ? (
                        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                          成交毛損益 {formatNumber(trade.gross_pnl)}；費用 {formatNumber(trade.fees)}；{trade.allocation_count ?? 0} 筆成交
                        </div>
                      ) : (
                        <div className="negative" style={{ fontSize: "0.75rem" }}>舊資料：缺少成交或商品證據，金額不可靠</div>
                      )}
                    </td>
                  </tr>
                );
              }) : (
                <tr><td colSpan={5} style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)" }}>尚無交易紀錄</td></tr>
              )}
            </tbody>
          </table>
        ) : (
          <div className="flex-center" style={{ height: "200px", color: "var(--text-muted)" }}>載入中…</div>
        )}
      </div>
    </div>
  );
}
