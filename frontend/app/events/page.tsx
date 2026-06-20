"use client";

import { useEffect, useState, useRef } from 'react';
import { API_BASE } from '@/lib/api';
import { Terminal, Play, Square } from 'lucide-react';

export default function Events() {
  const [events, setEvents] = useState<any[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Initial fetch of recent events
    fetch(`${API_BASE}/events?limit=50`)
      .then(res => res.json())
      .then(data => {
        setEvents(data.reverse()); // Show oldest first so new ones append at bottom
      })
      .catch(console.error);

    // Connect WebSocket
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws/events';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    
    ws.onmessage = (msg) => {
      if (isPaused) return;
      try {
        const data = JSON.parse(msg.data);
        setEvents(prev => [...prev, data].slice(-200)); // Keep last 200 events
      } catch (e) {}
    };

    return () => {
      ws.close();
    };
  }, [isPaused]);

  useEffect(() => {
    // Auto scroll to bottom
    if (!isPaused && eventsEndRef.current) {
      eventsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [events, isPaused]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: '100%' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '0.5rem' }}>系統日誌 (Events Trace)</h1>
          <p style={{ color: 'var(--text-muted)' }}>即時追蹤後台系統事件、API請求與策略動態</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem' }}>
            <span style={{ 
              width: '10px', height: '10px', borderRadius: '50%', 
              backgroundColor: isConnected ? 'var(--accent-success)' : 'var(--accent-danger)' 
            }}></span>
            {isConnected ? '已連線 (Connected)' : '連線中斷 (Disconnected)'}
          </div>
          <button 
            className={`btn ${isPaused ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => setIsPaused(!isPaused)}
            style={{ padding: '0.5rem 1rem' }}
          >
            {isPaused ? <Play size={16} /> : <Square size={16} />}
            {isPaused ? '繼續 (Resume)' : '暫停 (Pause)'}
          </button>
        </div>
      </header>

      <div className="glass-panel" style={{ 
        flex: 1, 
        backgroundColor: '#000000', 
        color: '#00ff00', 
        fontFamily: 'monospace', 
        padding: '1.5rem', 
        overflowY: 'auto',
        borderRadius: 'var(--radius-md)',
        minHeight: '60vh'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', color: '#888' }}>
          <Terminal size={16} /> <span>Waiting for events...</span>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
          {events.map((e, i) => (
            <div key={`${e.id}-${i}`} style={{ display: 'flex', gap: '1rem', wordBreak: 'break-all' }}>
              <span style={{ color: '#888', whiteSpace: 'nowrap' }}>{new Date(e.created_at).toLocaleTimeString()}</span>
              <span style={{ color: '#ffb86c', whiteSpace: 'nowrap', width: '100px', display: 'inline-block' }}>[{e.source}]</span>
              <span style={{ color: '#bd93f9', whiteSpace: 'nowrap', width: '120px', display: 'inline-block' }}>{e.type}</span>
              <span style={{ color: '#f8f8f2' }}>{JSON.stringify(e.payload)}</span>
            </div>
          ))}
          <div ref={eventsEndRef} />
        </div>
      </div>
    </div>
  );
}
