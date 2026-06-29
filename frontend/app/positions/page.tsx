"use client";

import { useState } from "react";
import useSWR from "swr";
import { ShieldAlert, ShieldCheck, Square, XCircle } from "lucide-react";

import {
  closeLogicalPosition,
  listLogicalPositions,
  type LogicalPositionUnit,
} from "@/lib/api";

function number(value: number | null | undefined, digits = 4): string {
  return value == null ? "-" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function Protection({ position }: { position: LogicalPositionUnit }) {
  const protection = position.protection;
  if (!protection) {
    return (
      <div style={{ color: "var(--accent-danger)", display: "flex", gap: "0.4rem", alignItems: "center" }}>
        <ShieldAlert size={16} /> No owned protection
      </div>
    );
  }
  const active = protection.status === "active";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
      <div style={{ color: active ? "var(--accent-success)" : "var(--accent-danger)", display: "flex", gap: "0.4rem", alignItems: "center" }}>
        {active ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
        {protection.status.replaceAll("_", " ")}
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
        Stop {number(protection.stop_loss)} · Qty {number(protection.quantity)}
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: "0.72rem", overflowWrap: "anywhere" }}>
        Algo {protection.algo_id}
        {protection.trigger_order_id ? ` · Trigger ${protection.trigger_order_id}` : ""}
      </div>
    </div>
  );
}

export default function Positions() {
  const { data, error, mutate, isLoading } = useSWR(
    "logical-positions",
    () => listLogicalPositions("all"),
    { refreshInterval: 5000 },
  );
  const [closingId, setClosingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  const requestClose = async (position: LogicalPositionUnit) => {
    if (!confirm(`Close logical unit ${position.id}?`)) return;
    setActionError("");
    setClosingId(position.id);
    try {
      await closeLogicalPosition(position.id, { confirm: true, reason: "operator dashboard close" });
      await mutate();
    } catch {
      setActionError(`Close request failed for ${position.id}.`);
    } finally {
      setClosingId(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      <header>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700 }}>Position Management</h1>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: "1rem", color: "var(--accent-danger)" }}>
          Position state is unavailable. Trading controls are disabled.
        </div>
      )}
      {actionError && (
        <div className="glass-panel" style={{ padding: "1rem", color: "var(--accent-danger)" }}>
          {actionError}
        </div>
      )}
      {isLoading && <div style={{ color: "var(--text-muted)" }}>Loading positions...</div>}
      {data?.length === 0 && <div style={{ color: "var(--text-muted)" }}>No logical positions.</div>}

      <div style={{ display: "grid", gap: "0.75rem" }}>
        {data?.map((position) => {
          const active = ["open", "reducing", "closing", "pending_open"].includes(position.status);
          const closeConditions = position.close_conditions ?? [];
          return (
            <article key={position.id} className="glass-panel" style={{ padding: "1rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1.3fr) repeat(3, minmax(120px, 1fr)) auto", gap: "1rem", alignItems: "center", overflowX: "auto" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 700 }}>
                    {position.inst_id}
                    <span className={`badge ${position.side === "long" ? "success" : "danger"}`}>{position.side}</span>
                  </div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.78rem", marginTop: "0.3rem", overflowWrap: "anywhere" }}>
                    {position.id} · {position.source}{position.strategy_id ? ` · ${position.strategy_id}` : ""}
                  </div>
                </div>
                <div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>Status</div>
                  <div style={{ display: "flex", gap: "0.35rem", alignItems: "center", marginTop: "0.25rem" }}>
                    {active ? <Square size={14} /> : <XCircle size={14} />}{position.status}
                  </div>
                </div>
                <div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>Unit</div>
                  <div style={{ marginTop: "0.25rem" }}>Entry {number(position.entry_price)}</div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>Remaining {number(position.remaining_quantity)}</div>
                </div>
                <Protection position={position} />
                <button
                  className="btn btn-outline"
                  disabled={position.status !== "open" || closingId === position.id}
                  onClick={() => requestClose(position)}
                >
                  <XCircle size={15} /> {closingId === position.id ? "Submitting" : "Close"}
                </button>
              </div>
              {closeConditions.length > 0 && (
                <div style={{ borderTop: "1px solid var(--border-color)", marginTop: "0.8rem", paddingTop: "0.7rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                  {closeConditions.map((condition) => (
                    <span key={condition.id} className="badge">
                      {(condition.purpose ?? "exit").replaceAll("_", " ")} · {condition.enabled !== false ? "enabled" : "disabled"}
                    </span>
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
