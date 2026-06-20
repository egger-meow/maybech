"use client";

import useSWR from 'swr';
import { fetcher } from '@/lib/api';

export default function History() {
  const { data: history } = useSWR('/trades/history?limit=100', fetcher, { refreshInterval: 10000 });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <header>
        <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '0.5rem' }}>歷史記錄 (Trade History)</h1>
        <p style={{ color: 'var(--text-muted)' }}>檢視已平倉的交易紀錄與績效</p>
      </header>

      <div className="glass-panel" style={{ padding: '1.5rem', overflowX: 'auto' }}>
        {history ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                <th style={{ padding: '1rem 0.5rem', fontWeight: 600, color: 'var(--text-secondary)' }}>時間 (Time)</th>
                <th style={{ padding: '1rem 0.5rem', fontWeight: 600, color: 'var(--text-secondary)' }}>標的 (Asset)</th>
                <th style={{ padding: '1rem 0.5rem', fontWeight: 600, color: 'var(--text-secondary)' }}>方向 (Side)</th>
                <th style={{ padding: '1rem 0.5rem', fontWeight: 600, color: 'var(--text-secondary)' }}>出場原因 (Reason)</th>
                <th style={{ padding: '1rem 0.5rem', fontWeight: 600, color: 'var(--text-secondary)', textAlign: 'right' }}>盈虧 (PnL)</th>
              </tr>
            </thead>
            <tbody>
              {history.length > 0 ? history.map((trade: any) => (
                <tr key={trade.id} style={{ borderBottom: '1px solid var(--border-color)', transition: 'background-color 0.2s' }}>
                  <td style={{ padding: '1rem 0.5rem', fontSize: '0.9rem' }}>
                    <div style={{ fontWeight: 500 }}>{new Date(trade.exit_time).toLocaleString()}</div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>策略: {trade.strategy_id}</div>
                  </td>
                  <td style={{ padding: '1rem 0.5rem', fontWeight: 600 }}>{trade.inst_id}</td>
                  <td style={{ padding: '1rem 0.5rem' }}>
                    <span className={`badge ${trade.side.toLowerCase() === 'long' || trade.side.toLowerCase() === 'buy' ? 'success' : 'danger'}`}>
                      {trade.side}
                    </span>
                  </td>
                  <td style={{ padding: '1rem 0.5rem', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
                    {trade.exit_reason}
                  </td>
                  <td style={{ padding: '1rem 0.5rem', textAlign: 'right', fontWeight: 600, color: (trade.pnl || 0) >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)' }}>
                    {(trade.pnl || 0) >= 0 ? '+' : ''}${trade.pnl?.toFixed(2) || '0.00'} 
                    <div style={{ fontSize: '0.8rem' }}>
                      ({(trade.pnl_pct || 0).toFixed(2)}%)
                    </div>
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={5} style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>無歷史交易 (No trade history)</td>
                </tr>
              )}
            </tbody>
          </table>
        ) : (
          <div className="flex-center" style={{ height: '200px', color: 'var(--text-muted)' }}>載入中...</div>
        )}
      </div>
    </div>
  );
}
