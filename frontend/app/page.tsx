"use client";

import useSWR from "swr";
import { getAccountSnapshot, getBtcRegime } from "@/lib/api";
import { TrendingUp, TrendingDown, DollarSign, Activity } from "lucide-react";

function formatCurrency(value: unknown): string {
  const numberValue = Number(value ?? 0);
  if (!Number.isFinite(numberValue)) {
    return "0.00";
  }
  return numberValue.toFixed(2);
}

function formatLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "N/A";
  }
  return String(value);
}

export default function Dashboard() {
  const { data: account, error: accountError } = useSWR("account-snapshot", getAccountSnapshot, { refreshInterval: 5000 });
  const { data: regime, error: regimeError } = useSWR("btc-regime", getBtcRegime, { refreshInterval: 5000 });

  const loading = !account && !accountError;
  const regimeLoading = !regime && !regimeError;
  const unrealizedPnl = Number(account?.summary?.unrealized_pnl ?? 0);
  const isBullish = regime?.direction === "bullish";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <header>
        <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>Dashboard</h1>
        <p style={{ color: "var(--text-muted)" }}>Runtime account and market state.</p>
      </header>

      {loading ? (
        <div className="flex-center" style={{ height: "200px", color: "var(--text-muted)" }}>Loading...</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "1.5rem" }}>
          <div className="glass-panel" style={{ padding: "1.5rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem", color: "var(--text-secondary)" }}>
              <DollarSign size={20} />
              <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Equity</h2>
            </div>
            <div style={{ fontSize: "2.5rem", fontWeight: 700, color: "var(--text-primary)" }}>
              ${formatCurrency(account?.summary?.total_equity)}
            </div>
            <div style={{ marginTop: "0.5rem", color: "var(--text-muted)", fontSize: "0.9rem" }}>
              Available: ${formatCurrency(account?.summary?.available_equity)}
            </div>
          </div>

          <div className="glass-panel" style={{ padding: "1.5rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem", color: "var(--text-secondary)" }}>
              <Activity size={20} />
              <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Unrealized PnL</h2>
            </div>
            <div
              style={{
                fontSize: "2.5rem",
                fontWeight: 700,
                color: unrealizedPnl >= 0 ? "var(--accent-success)" : "var(--accent-danger)",
              }}
            >
              {unrealizedPnl >= 0 ? "+" : ""}${formatCurrency(unrealizedPnl)}
            </div>
          </div>
        </div>
      )}

      <h2 style={{ fontSize: "1.5rem", fontWeight: 600, marginTop: "1rem" }}>Market Overview</h2>

      {regimeLoading ? (
        <div className="flex-center" style={{ height: "100px", color: "var(--text-muted)" }}>Loading...</div>
      ) : (
        <div className="glass-panel" style={{ padding: "1.5rem", display: "flex", alignItems: "center", gap: "2rem", flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: "240px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              {isBullish ? <TrendingUp size={24} color="var(--accent-success)" /> : <TrendingDown size={24} color="var(--accent-danger)" />}
              <span style={{ fontSize: "1.25rem", fontWeight: 600 }}>
                BTC Direction: {formatLabel(regime?.direction)}
              </span>
            </div>
            <p style={{ color: "var(--text-muted)" }}>BTC regime data from the backend market service.</p>
          </div>

          <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
            <div style={{ padding: "1rem", backgroundColor: "var(--bg-secondary)", borderRadius: "var(--radius-md)", textAlign: "center", minWidth: "120px" }}>
              <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "0.25rem" }}>Strength</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 600, color: "var(--text-primary)" }}>{formatLabel(regime?.strength)}</div>
            </div>
            <div style={{ padding: "1rem", backgroundColor: "var(--bg-secondary)", borderRadius: "var(--radius-md)", textAlign: "center", minWidth: "120px" }}>
              <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "0.25rem" }}>Impulse</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 600, color: "var(--text-primary)" }}>{formatLabel(regime?.impulse)}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
