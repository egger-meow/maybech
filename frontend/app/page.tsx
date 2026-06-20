"use client";

import useSWR from 'swr';
import { fetcher } from '@/lib/api';
import { TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react';

export default function Dashboard() {
  const { data: account, error: accountError } = useSWR('/account/snapshot', fetcher, { refreshInterval: 5000 });
  const { data: regime, error: regimeError } = useSWR('/market/btc-regime', fetcher, { refreshInterval: 5000 });

  const loading = !account && !accountError;
  const regimeLoading = !regime && !regimeError;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <header>
        <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '0.5rem' }}>儀表板 (Dashboard)</h1>
        <p style={{ color: 'var(--text-muted)' }}>即時帳戶狀態與市場概況</p>
      </header>

      {loading ? (
        <div className="flex-center" style={{ height: '200px', color: 'var(--text-muted)' }}>載入中...</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
          
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', color: 'var(--text-secondary)' }}>
              <DollarSign size={20} />
              <h2 style={{ fontSize: '1.1rem', fontWeight: 600 }}>總資產 (Equity)</h2>
            </div>
            <div style={{ fontSize: '2.5rem', fontWeight: 700, color: 'var(--text-primary)' }}>
              ${account?.summary?.equity?.toFixed(2) || '0.00'}
            </div>
            <div style={{ marginTop: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              可用餘額: ${account?.summary?.available?.toFixed(2) || '0.00'}
            </div>
          </div>

          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', color: 'var(--text-secondary)' }}>
              <Activity size={20} />
              <h2 style={{ fontSize: '1.1rem', fontWeight: 600 }}>未實現盈虧 (Unrealized PnL)</h2>
            </div>
            <div style={{ 
              fontSize: '2.5rem', 
              fontWeight: 700, 
              color: (account?.summary?.unrealized_pnl || 0) >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)' 
            }}>
              {(account?.summary?.unrealized_pnl || 0) >= 0 ? '+' : ''}
              ${account?.summary?.unrealized_pnl?.toFixed(2) || '0.00'}
            </div>
          </div>

        </div>
      )}

      <h2 style={{ fontSize: '1.5rem', fontWeight: 600, marginTop: '1rem' }}>市場概況 (Market Overview)</h2>
      
      {regimeLoading ? (
        <div className="flex-center" style={{ height: '100px', color: 'var(--text-muted)' }}>載入中...</div>
      ) : (
        <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', alignItems: 'center', gap: '2rem' }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              {regime?.direction === 'bullish' ? <TrendingUp size={24} color="var(--accent-success)" /> : <TrendingDown size={24} color="var(--accent-danger)" />}
              <span style={{ fontSize: '1.25rem', fontWeight: 600 }}>
                BTC 趨勢: {regime?.direction === 'bullish' ? '看漲 (Bullish)' : '看跌 (Bearish)'}
              </span>
            </div>
            <p style={{ color: 'var(--text-muted)' }}>目前的市場結構由比特幣主導。請根據市場趨勢調整您的策略。</p>
          </div>
          
          <div style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ padding: '1rem', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', textAlign: 'center' }}>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>趨勢強度 (Strength)</div>
              <div style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text-primary)' }}>{regime?.strength?.toFixed(2) || 'N/A'}</div>
            </div>
            <div style={{ padding: '1rem', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', textAlign: 'center' }}>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>動能 (Impulse)</div>
              <div style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text-primary)' }}>{regime?.impulse?.toFixed(2) || 'N/A'}</div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
