"use client";

import useSWR from "swr";
import { listTradeHistory } from "@/lib/api";

function formatNumber(value: number | null | undefined): string {
  return Number(value ?? 0).toFixed(2);
}

export default function History() {
  const { data: history, error } = useSWR("trade-history", () => listTradeHistory(100), { refreshInterval: 10000 });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <header>
        <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>Trade History</h1>
        <p style={{ color: "var(--text-muted)" }}>Closed positions recorded by the runtime API.</p>
      </header>

      <div className="glass-panel" style={{ padding: "1.5rem", overflowX: "auto" }}>
        {error ? (
          <div style={{ padding: "1rem", color: "var(--accent-danger)" }}>
            Backend history API unavailable. Check the API process and CORS configuration.
          </div>
        ) : history ? (
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th style={{ padding: "1rem 0.5rem", fontWeight: 600, color: "var(--text-secondary)" }}>Time</th>
                <th style={{ padding: "1rem 0.5rem", fontWeight: 600, color: "var(--text-secondary)" }}>Asset</th>
                <th style={{ padding: "1rem 0.5rem", fontWeight: 600, color: "var(--text-secondary)" }}>Side</th>
                <th style={{ padding: "1rem 0.5rem", fontWeight: 600, color: "var(--text-secondary)" }}>Reason</th>
                <th style={{ padding: "1rem 0.5rem", fontWeight: 600, color: "var(--text-secondary)", textAlign: "right" }}>PnL</th>
              </tr>
            </thead>
            <tbody>
              {history.length > 0 ? history.map((trade) => {
                const pnl = trade.pnl ?? 0;
                const side = trade.side.toLowerCase();
                return (
                  <tr key={trade.id} style={{ borderBottom: "1px solid var(--border-color)", transition: "background-color 0.2s" }}>
                    <td style={{ padding: "1rem 0.5rem", fontSize: "0.9rem" }}>
                      <div style={{ fontWeight: 500 }}>{trade.exit_time ? new Date(trade.exit_time).toLocaleString() : "N/A"}</div>
                      <div style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>Strategy: {trade.strategy_id}</div>
                    </td>
                    <td style={{ padding: "1rem 0.5rem", fontWeight: 600 }}>{trade.inst_id}</td>
                    <td style={{ padding: "1rem 0.5rem" }}>
                      <span className={`badge ${side === "long" || side === "buy" ? "success" : "danger"}`}>{trade.side}</span>
                    </td>
                    <td style={{ padding: "1rem 0.5rem", fontSize: "0.9rem", color: "var(--text-muted)" }}>{trade.exit_reason}</td>
                    <td style={{ padding: "1rem 0.5rem", textAlign: "right", fontWeight: 600, color: pnl >= 0 ? "var(--accent-success)" : "var(--accent-danger)" }}>
                      {pnl >= 0 ? "+" : ""}${formatNumber(pnl)}
                      <div style={{ fontSize: "0.8rem" }}>({formatNumber(trade.pnl_pct)}%)</div>
                    </td>
                  </tr>
                );
              }) : (
                <tr>
                  <td colSpan={5} style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)" }}>No trade history</td>
                </tr>
              )}
            </tbody>
          </table>
        ) : (
          <div className="flex-center" style={{ height: "200px", color: "var(--text-muted)" }}>Loading...</div>
        )}
      </div>
    </div>
  );
}
