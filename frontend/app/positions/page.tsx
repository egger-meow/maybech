"use client";

import useSWR from 'swr';
import { fetcher, postData, deleteData } from '@/lib/api';
import { useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';

export default function Positions() {
  const { data: trades, mutate } = useSWR('/trades/open', fetcher, { refreshInterval: 5000 });
  const [selectedTrade, setSelectedTrade] = useState<any>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Form states
  const [ruleName, setRuleName] = useState('');
  const [groupOp, setGroupOp] = useState('and');
  const [rules, setRules] = useState<any[]>([{ target: 'self', metric: 'price', operator: 'less_than', value: 0 }]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const openModal = (trade: any) => {
    setSelectedTrade(trade);
    setRuleName('');
    setGroupOp('and');
    setRules([{ target: 'self', metric: 'price', operator: 'less_than', value: 0 }]);
    setIsModalOpen(true);
  };

  const addRuleCondition = () => {
    setRules([...rules, { target: 'self', metric: 'price', operator: 'less_than', value: 0 }]);
  };

  const updateRuleCondition = (index: number, key: string, value: any) => {
    const newRules = [...rules];
    newRules[index][key] = value;
    setRules(newRules);
  };

  const removeRuleCondition = (index: number) => {
    setRules(rules.filter((_, i) => i !== index));
  };

  const handleAddRuleGroup = async () => {
    if (!selectedTrade) return;
    setIsSubmitting(true);
    try {
      await postData(`/trades/${selectedTrade.id}/rules`, {
        rule_group: {
          name: ruleName || '自訂規則',
          operator: groupOp,
          rules: rules.map(r => ({ ...r, value: parseFloat(r.value) }))
        },
        enabled: true
      });
      await mutate();
      setIsModalOpen(false);
    } catch (e) {
      alert('新增失敗 (Add failed)');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteRuleGroup = async (tradeId: string, groupId: string) => {
    if (!confirm('確定刪除此規則？ (Are you sure?)')) return;
    try {
      await deleteData(`/trades/${tradeId}/rules/${groupId}`);
      await mutate();
    } catch (e) {
      alert('刪除失敗 (Delete failed)');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <header>
        <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '0.5rem' }}>倉位管理 (Positions)</h1>
        <p style={{ color: 'var(--text-muted)' }}>動態管理各別倉位的出場條件 (Stop-Loss / Take-Profit / Signals)</p>
      </header>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        {trades ? trades.map((trade: any) => (
          <div key={trade.id} className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontSize: '1.25rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {trade.inst_id}
                  <span className={`badge ${trade.side.toLowerCase() === 'long' || trade.side.toLowerCase() === 'buy' ? 'success' : 'danger'}`}>
                    {trade.side}
                  </span>
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
                  策略: {trade.strategy_id} | 入場價: ${trade.entry_price?.toFixed(4)}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, color: (trade.pnl || 0) >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)' }}>
                  {(trade.pnl || 0) >= 0 ? '+' : ''}${trade.pnl?.toFixed(2) || '0.00'} ({(trade.pnl_pct || 0).toFixed(2)}%)
                </div>
              </div>
            </div>

            <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>觸發規則 (Active Rules)</h3>
                <button className="btn btn-outline" onClick={() => openModal(trade)} style={{ padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}>
                  <Plus size={14} /> 新增規則 (Add Rule)
                </button>
              </div>
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {trade.active_rules && trade.active_rules.length > 0 ? trade.active_rules.map((ar: any) => (
                  <div key={ar.group.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'var(--bg-secondary)', padding: '0.75rem', borderRadius: 'var(--radius-sm)' }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{ar.group.name} {ar.group.operator === 'or' ? '(OR)' : '(AND)'}</div>
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>
                        {ar.group.rules.map((r: any, idx: number) => (
                          <span key={r.id}>
                            {idx > 0 ? ` ${ar.group.operator.toUpperCase()} ` : ''}
                            [{r.target}] {r.metric} {r.operator === 'greater_than' ? '>' : '<'} {r.value}
                          </span>
                        ))}
                      </div>
                    </div>
                    <button className="btn btn-outline" style={{ border: 'none', color: 'var(--accent-danger)', padding: '0.5rem' }} onClick={() => handleDeleteRuleGroup(trade.id, ar.group.id)}>
                      <Trash2 size={16} />
                    </button>
                  </div>
                )) : (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>無綁定規則 (No active rules)</div>
                )}
              </div>
            </div>
          </div>
        )) : (
          <div className="flex-center" style={{ height: '100px', color: 'var(--text-muted)' }}>載入中...</div>
        )}
      </div>

      {isModalOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 50, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
          <div className="glass-panel" style={{ width: '100%', maxWidth: '500px', padding: '2rem', display: 'flex', flexDirection: 'column', gap: '1.5rem', maxHeight: '90vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 600 }}>為 {selectedTrade?.inst_id} 新增規則</h2>
              <button className="btn btn-outline" style={{ border: 'none', padding: '0.25rem' }} onClick={() => setIsModalOpen(false)}>
                <X size={20} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <label style={{ fontSize: '0.9rem', fontWeight: 500 }}>規則群組名稱</label>
              <input type="text" value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="e.g. 停損 (Stop Loss)" />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <label style={{ fontSize: '0.9rem', fontWeight: 500 }}>邏輯運算子 (Operator)</label>
              <select value={groupOp} onChange={e => setGroupOp(e.target.value)}>
                <option value="and">全部滿足 (AND)</option>
                <option value="or">任一滿足 (OR)</option>
              </select>
            </div>

            <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600 }}>條件列表 (Conditions)</span>
                <button className="btn btn-outline" onClick={addRuleCondition} style={{ padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}>
                  <Plus size={14} /> 加入條件
                </button>
              </div>

              {rules.map((r, i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr auto', gap: '0.5rem', alignItems: 'center' }}>
                  <select value={r.target} onChange={e => updateRuleCondition(i, 'target', e.target.value)}>
                    <option value="self">目前幣種 (Self)</option>
                    <option value="BTC-USDT">比特幣 (BTC-USDT)</option>
                  </select>
                  <select value={r.metric} onChange={e => updateRuleCondition(i, 'metric', e.target.value)}>
                    <option value="price">價格 (Price)</option>
                    <option value="pnl_pct">未實現盈虧% (PnL%)</option>
                    <option value="velocity_5m">5分變化率 (Velocity)</option>
                  </select>
                  <select value={r.operator} onChange={e => updateRuleCondition(i, 'operator', e.target.value)}>
                    <option value="less_than">小於 (&lt;)</option>
                    <option value="greater_than">大於 (&gt;)</option>
                  </select>
                  <input type="number" step="any" value={r.value} onChange={e => updateRuleCondition(i, 'value', e.target.value)} />
                  <button className="btn btn-outline" style={{ padding: '0.5rem', color: 'var(--accent-danger)', border: 'none' }} onClick={() => removeRuleCondition(i)}>
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '1rem' }}>
              <button className="btn btn-outline" onClick={() => setIsModalOpen(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleAddRuleGroup} disabled={isSubmitting || rules.length === 0}>
                {isSubmitting ? '儲存中...' : '確認儲存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
