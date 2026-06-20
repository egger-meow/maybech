"use client";

import useSWR from 'swr';
import { fetcher, postData } from '@/lib/api';
import { Play, Square, Settings, CheckCircle2, AlertCircle } from 'lucide-react';
import { useState } from 'react';

export default function Strategies() {
  const { data: services, mutate: mutateServices } = useSWR('/services', fetcher, { refreshInterval: 5000 });
  const { data: decisions } = useSWR('/strategy/decisions', fetcher, { refreshInterval: 5000 });
  
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const toggleService = async (name: string, isRunning: boolean) => {
    setLoadingAction(name);
    try {
      const action = isRunning ? 'disable' : 'enable';
      await postData(`/services/${name}/${action}`);
      await mutateServices();
    } catch (e) {
      console.error('Failed to toggle service', e);
      alert('操作失敗 (Operation failed)');
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <header>
        <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '0.5rem' }}>策略管理 (Strategies & Services)</h1>
        <p style={{ color: 'var(--text-muted)' }}>控制後台服務及策略決策</p>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
        {/* Services List */}
        <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}>
            <Settings size={20} />
            <h2 style={{ fontSize: '1.2rem', fontWeight: 600 }}>系統服務狀態 (Daemon Services)</h2>
          </div>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {services ? Object.entries(services).map(([name, status]: [string, any]) => {
              const isRunning = status?.state === 'running';
              const isLoading = loadingAction === name;

              return (
                <div key={name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem', backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '1rem' }}>{name}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.8rem', color: isRunning ? 'var(--accent-success)' : 'var(--text-muted)', marginTop: '0.25rem' }}>
                      {isRunning ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                      {isRunning ? '運行中 (Running)' : '已停止 (Stopped)'}
                    </div>
                  </div>
                  <button 
                    className={`btn ${isRunning ? 'btn-danger' : 'btn-primary'}`} 
                    onClick={() => toggleService(name, isRunning)}
                    disabled={isLoading}
                  >
                    {isRunning ? <Square size={16} /> : <Play size={16} />}
                    {isLoading ? '處理中...' : (isRunning ? '停止' : '啟動')}
                  </button>
                </div>
              );
            }) : (
              <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '1rem' }}>載入中...</div>
            )}
          </div>
        </div>

        {/* Recent Decisions */}
        <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <h2 style={{ fontSize: '1.2rem', fontWeight: 600, color: 'var(--text-secondary)' }}>近期策略決策 (Recent Decisions)</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', flex: 1, overflowY: 'auto', maxHeight: '400px' }}>
            {decisions && decisions.length > 0 ? decisions.map((d: any, idx: number) => (
              <div key={idx} style={{ padding: '0.75rem', backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)', fontSize: '0.9rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                  <span style={{ fontWeight: 600 }}>{d.strategy_id}</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{new Date(d.timestamp).toLocaleString()}</span>
                </div>
                <div>標的: {d.target}</div>
                <div style={{ color: d.action === 'LONG' || d.action === 'BUY' ? 'var(--accent-success)' : d.action === 'SHORT' || d.action === 'SELL' ? 'var(--accent-danger)' : 'var(--text-muted)' }}>
                  動作: {d.action}
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>原因: {d.reason}</div>
              </div>
            )) : (
              <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '1rem' }}>無近期決策 (No recent decisions)</div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
