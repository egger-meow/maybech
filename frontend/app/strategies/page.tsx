"use client";

import useSWR from "swr";
import {
  disableService,
  enableService,
  listServices,
  listStrategies,
  listPersistedStrategyDecisions,
  type ServiceStatus,
} from "@/lib/api";
import { Play, Square, Settings, CheckCircle2, AlertCircle } from "lucide-react";
import { useState } from "react";

export default function Strategies() {
  const { data: services, error: servicesError, mutate: mutateServices } = useSWR<Record<string, ServiceStatus>>(
    "services",
    listServices,
    { refreshInterval: 5000 },
  );
  const { data: strategies, error: strategiesError } = useSWR("strategies", listStrategies);
  const selectedStrategyId = strategies?.find((strategy) => strategy.enabled)?.id ?? strategies?.[0]?.id;
  const { data: decisions, error: decisionsError } = useSWR(
    selectedStrategyId ? ["strategy-decisions", selectedStrategyId] : null,
    () => listPersistedStrategyDecisions(selectedStrategyId!, { limit: 50 }),
    { refreshInterval: 5000 },
  );

  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const toggleService = async (name: string, isRunning: boolean) => {
    setLoadingAction(name);
    try {
      if (isRunning) {
        await disableService(name);
      } else {
        await enableService(name);
      }
      await mutateServices();
    } catch (e) {
      console.error("Failed to toggle service", e);
      alert("Operation failed");
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <header>
        <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>Strategies & Services</h1>
        <p style={{ color: "var(--text-muted)" }}>Control daemon services and inspect recent strategy decisions.</p>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "1.5rem" }}>
        <div className="glass-panel" style={{ padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "var(--text-secondary)" }}>
            <Settings size={20} />
            <h2 style={{ fontSize: "1.2rem", fontWeight: 600 }}>Daemon Services</h2>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {servicesError ? (
              <div style={{ color: "var(--accent-danger)", padding: "1rem" }}>Backend services API unavailable.</div>
            ) : services ? Object.entries(services).map(([name, status]) => {
              const isRunning = status?.active === true;
              const isLoading = loadingAction === name;

              return (
                <div key={name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem", padding: "1rem", backgroundColor: "var(--bg-primary)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-color)" }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: "1rem" }}>{name}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.25rem", fontSize: "0.8rem", color: isRunning ? "var(--accent-success)" : "var(--text-muted)", marginTop: "0.25rem" }}>
                      {isRunning ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                      {isRunning ? "Running" : "Stopped"}
                    </div>
                  </div>
                  <button
                    className={`btn ${isRunning ? "btn-danger" : "btn-primary"}`}
                    onClick={() => toggleService(name, isRunning)}
                    disabled={isLoading}
                  >
                    {isRunning ? <Square size={16} /> : <Play size={16} />}
                    {isLoading ? "Working..." : (isRunning ? "Stop" : "Start")}
                  </button>
                </div>
              );
            }) : (
              <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "1rem" }}>Loading...</div>
            )}
          </div>
        </div>

        <div className="glass-panel" style={{ padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
          <h2 style={{ fontSize: "1.2rem", fontWeight: 600, color: "var(--text-secondary)" }}>Recent Decisions</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", flex: 1, overflowY: "auto", maxHeight: "400px" }}>
            {strategiesError || decisionsError ? (
              <div style={{ color: "var(--accent-danger)", padding: "1rem" }}>Strategy decision API unavailable.</div>
            ) : decisions && decisions.length > 0 ? decisions.map((d, idx) => (
              <div key={d.id ?? idx} style={{ padding: "0.75rem", backgroundColor: "var(--bg-primary)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-color)", fontSize: "0.9rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", marginBottom: "0.25rem" }}>
                  <span style={{ fontWeight: 600 }}>{d.pair ?? "strategy"}</span>
                  <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
                    {d.time ? new Date(d.time).toLocaleString() : ""}
                  </span>
                </div>
                <div>Signal: {d.signal ?? "N/A"}</div>
                <div style={{ color: d.allowed === true ? "var(--accent-success)" : d.allowed === false ? "var(--accent-danger)" : "var(--text-muted)" }}>
                  Decision: {d.allowed === true ? "Allowed" : d.allowed === false ? "Blocked" : "N/A"}
                </div>
                <div>Execution: {d.execution_status ?? "not started"}</div>
                <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", marginTop: "0.25rem" }}>Reason: {d.reason ?? "N/A"}</div>
              </div>
            )) : (
              <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "1rem" }}>No recent decisions</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
