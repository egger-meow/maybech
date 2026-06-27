"use client";

import useSWR from "swr";
import {
  attachTradeRule,
  deleteTradeRule,
  listOpenTrades,
  type PositionRule,
  type TradeDetail,
} from "@/lib/api";
import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";

type RuleCondition = {
  target: string;
  metric: NonNullable<PositionRule["metric"]>;
  operator: NonNullable<PositionRule["operator"]>;
  value: number | string;
};

const EMPTY_RULE: RuleCondition = {
  target: "self",
  metric: "price",
  operator: "less_than",
  value: 0,
};

function formatNumber(value: number | null | undefined, digits = 2): string {
  return Number(value ?? 0).toFixed(digits);
}

export default function Positions() {
  const { data: trades, error, mutate } = useSWR("open-trades", listOpenTrades, { refreshInterval: 5000 });
  const [selectedTrade, setSelectedTrade] = useState<TradeDetail | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [ruleName, setRuleName] = useState("");
  const [groupOp, setGroupOp] = useState<"and" | "or">("and");
  const [rules, setRules] = useState<RuleCondition[]>([{ ...EMPTY_RULE }]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const openModal = (trade: TradeDetail) => {
    setSelectedTrade(trade);
    setRuleName("");
    setGroupOp("and");
    setRules([{ ...EMPTY_RULE }]);
    setIsModalOpen(true);
  };

  const addRuleCondition = () => {
    setRules((current) => [...current, { ...EMPTY_RULE }]);
  };

  const updateRuleCondition = <K extends keyof RuleCondition>(index: number, key: K, value: RuleCondition[K]) => {
    setRules((current) => current.map((rule, i) => (i === index ? { ...rule, [key]: value } : rule)));
  };

  const removeRuleCondition = (index: number) => {
    setRules((current) => current.filter((_, i) => i !== index));
  };

  const handleAddRuleGroup = async () => {
    if (!selectedTrade) return;
    setIsSubmitting(true);
    try {
      await attachTradeRule(selectedTrade.id, {
        rule_group: {
          name: ruleName || "Manual rule",
          operator: groupOp,
          rules: rules.map((rule) => ({ ...rule, value: Number(rule.value) })),
        },
        enabled: true,
      });
      await mutate();
      setIsModalOpen(false);
    } catch (error: unknown) {
      console.error("Failed to add rule group", error);
      alert("Add failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteRuleGroup = async (tradeId: string, groupId: string) => {
    if (!confirm("Delete this rule group?")) return;
    try {
      await deleteTradeRule(tradeId, groupId);
      await mutate();
    } catch (error: unknown) {
      console.error("Failed to delete rule group", error);
      alert("Delete failed");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
      <header>
        <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>Positions</h1>
        <p style={{ color: "var(--text-muted)" }}>Open trades and attached dynamic exit rules.</p>
      </header>

      <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
        {error ? (
          <div className="glass-panel" style={{ padding: "1.5rem", color: "var(--accent-danger)" }}>
            Backend positions API unavailable. Check the API process and CORS configuration.
          </div>
        ) : trades ? trades.map((trade) => {
          const side = trade.side.toLowerCase();
          const pnl = trade.pnl ?? 0;
          const activeRules = trade.active_rules ?? [];
          return (
            <div key={trade.id} className="glass-panel" style={{ padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
                <div>
                  <div style={{ fontSize: "1.25rem", fontWeight: 700, display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    {trade.inst_id}
                    <span className={`badge ${side === "long" || side === "buy" ? "success" : "danger"}`}>{trade.side}</span>
                  </div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.9rem", marginTop: "0.25rem" }}>
                    Strategy: {trade.strategy_id} | Entry: ${formatNumber(trade.entry_price, 4)}
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: "1.5rem", fontWeight: 700, color: pnl >= 0 ? "var(--accent-success)" : "var(--accent-danger)" }}>
                    {pnl >= 0 ? "+" : ""}${formatNumber(pnl)} ({formatNumber(trade.pnl_pct)}%)
                  </div>
                </div>
              </div>

              <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "1rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", gap: "1rem" }}>
                  <h3 style={{ fontSize: "1rem", fontWeight: 600 }}>Active Rules</h3>
                  <button className="btn btn-outline" onClick={() => openModal(trade)} style={{ padding: "0.25rem 0.5rem", fontSize: "0.8rem" }}>
                    <Plus size={14} /> Add Rule
                  </button>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                  {activeRules.length > 0 ? activeRules.map((activeRule) => (
                    <div key={activeRule.group.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem", backgroundColor: "var(--bg-secondary)", padding: "0.75rem", borderRadius: "var(--radius-sm)" }}>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{activeRule.group.name} ({activeRule.group.operator.toUpperCase()})</div>
                        <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", marginTop: "0.25rem" }}>
                          {activeRule.group.rules.map((rule, idx) => (
                            <span key={rule.id}>
                              {idx > 0 ? ` ${activeRule.group.operator.toUpperCase()} ` : ""}
                              [{rule.target}] {rule.metric} {rule.operator === "greater_than" ? ">" : "<"} {rule.value}
                            </span>
                          ))}
                        </div>
                      </div>
                      <button className="btn btn-outline" style={{ border: "none", color: "var(--accent-danger)", padding: "0.5rem" }} onClick={() => handleDeleteRuleGroup(trade.id, activeRule.group.id)} aria-label="Delete rule group">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  )) : (
                    <div style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>No active rules</div>
                  )}
                </div>
              </div>
            </div>
          );
        }) : (
          <div className="flex-center" style={{ height: "100px", color: "var(--text-muted)" }}>Loading...</div>
        )}
      </div>

      {isModalOpen && (
        <div style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.5)", zIndex: 50, display: "flex", justifyContent: "center", alignItems: "center" }}>
          <div className="glass-panel" style={{ width: "100%", maxWidth: "720px", padding: "2rem", display: "flex", flexDirection: "column", gap: "1.5rem", maxHeight: "90vh", overflowY: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
              <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Add Rule for {selectedTrade?.inst_id}</h2>
              <button className="btn btn-outline" style={{ border: "none", padding: "0.25rem" }} onClick={() => setIsModalOpen(false)} aria-label="Close modal">
                <X size={20} />
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              <label style={{ fontSize: "0.9rem", fontWeight: 500 }}>Rule group name</label>
              <input type="text" value={ruleName} onChange={(event) => setRuleName(event.target.value)} placeholder="Stop loss" />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              <label style={{ fontSize: "0.9rem", fontWeight: 500 }}>Group operator</label>
              <select value={groupOp} onChange={(event) => setGroupOp(event.target.value === "or" ? "or" : "and")}>
                <option value="and">AND</option>
                <option value="or">OR</option>
              </select>
            </div>

            <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
                <span style={{ fontWeight: 600 }}>Conditions</span>
                <button className="btn btn-outline" onClick={addRuleCondition} style={{ padding: "0.25rem 0.5rem", fontSize: "0.8rem" }}>
                  <Plus size={14} /> Add Condition
                </button>
              </div>

              {rules.map((rule, index) => (
                <div key={index} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr auto", gap: "0.5rem", alignItems: "center" }}>
                  <select value={rule.target} onChange={(event) => updateRuleCondition(index, "target", event.target.value)}>
                    <option value="self">Self</option>
                    <option value="BTC-USDT-SWAP">BTC-USDT-SWAP</option>
                  </select>
                  <select value={rule.metric} onChange={(event) => updateRuleCondition(index, "metric", event.target.value as RuleCondition["metric"])}>
                    <option value="price">Price</option>
                    <option value="pnl_pct">PnL%</option>
                    <option value="velocity_1m">Velocity 1m</option>
                    <option value="velocity_5m">Velocity 5m</option>
                    <option value="velocity_10m">Velocity 10m</option>
                  </select>
                  <select value={rule.operator} onChange={(event) => updateRuleCondition(index, "operator", event.target.value as RuleCondition["operator"])}>
                    <option value="less_than">Less Than</option>
                    <option value="greater_than">Greater Than</option>
                  </select>
                  <input type="number" step="any" value={rule.value} onChange={(event) => updateRuleCondition(index, "value", event.target.value)} />
                  <button className="btn btn-outline" style={{ padding: "0.5rem", color: "var(--accent-danger)", border: "none" }} onClick={() => removeRuleCondition(index)} aria-label="Remove condition">
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "1rem", marginTop: "1rem" }}>
              <button className="btn btn-outline" onClick={() => setIsModalOpen(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleAddRuleGroup} disabled={isSubmitting || rules.length === 0}>
                {isSubmitting ? "Saving..." : "Save Rule"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
