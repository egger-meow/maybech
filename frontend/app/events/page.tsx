"use client";

import { useEffect, useRef, useState } from "react";
import { listRecentEvents, wsUrl, type RuntimeEvent } from "@/lib/api";
import { Terminal, Play, Square } from "lucide-react";

export default function Events() {
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isAutoScroll, setIsAutoScroll] = useState(true);
  const [connectionError, setConnectionError] = useState("");
  const eventLogRef = useRef<HTMLDivElement>(null);
  const isPausedRef = useRef(isPaused);

  useEffect(() => {
    isPausedRef.current = isPaused;
  }, [isPaused]);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;

    listRecentEvents(50)
      .then((data) => {
        setEvents(data.reverse());
      })
      .catch(() => setConnectionError("無法讀取受保護的事件紀錄。"));

    const connect = () => {
      if (!active) return;
      socket = new WebSocket(wsUrl("/ws/events"));
      socket.onopen = () => {
        reconnectAttempt = 0;
        setConnectionError("");
        setIsConnected(true);
      };
      socket.onclose = (event) => {
        setIsConnected(false);
        if (!active) return;
        if (event.code === 1008) {
          window.dispatchEvent(new Event("maybech:authentication-required"));
          return;
        }
        const delay = Math.min(1000 * (2 ** reconnectAttempt), 10_000);
        reconnectAttempt += 1;
        setConnectionError(`事件串流已中斷，${Math.ceil(delay / 1000)} 秒後重連。`);
        reconnectTimer = setTimeout(connect, delay);
      };
      socket.onmessage = (msg) => {
        if (isPausedRef.current) return;
        try {
          const data = JSON.parse(msg.data) as RuntimeEvent;
          setEvents((prev) => [...prev, data].slice(-200));
        } catch {
          setConnectionError("收到無法解析的事件資料。");
        }
      };
    };
    connect();

    return () => {
      active = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    if (!isPaused && isAutoScroll && eventLogRef.current) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight;
    }
  }, [events, isPaused, isAutoScroll]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setIsAutoScroll(isAtBottom);
  };

  return (
    <div className="events-page">
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "1rem" }}>
        <div>
          <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>即時事件</h1>
          <p style={{ color: "var(--text-muted)" }}>來自執行環境 API 的即時 daemon 事件；重新啟動後的稽核請以持久事件為準。</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
            <span
              style={{
                width: "10px",
                height: "10px",
                borderRadius: "50%",
                backgroundColor: isConnected ? "var(--accent-success)" : "var(--accent-danger)",
              }}
            />
            {isConnected ? "已連線" : "已斷線"}
          </div>
          <button
            className={`btn ${isPaused ? "btn-primary" : "btn-outline"}`}
            onClick={() => {
              setIsPaused(!isPaused);
              if (isPaused) {
                // If resuming, snap back to bottom auto-scroll
                setIsAutoScroll(true);
              }
            }}
            style={{ padding: "0.5rem 1rem" }}
          >
            {isPaused ? <Play size={16} /> : <Square size={16} />}
            {isPaused ? "繼續" : "暫停"}
          </button>
        </div>
      </header>

      {connectionError && <div className="error-state">{connectionError}</div>}

      <div
        ref={eventLogRef}
        className="glass-panel event-log"
        onScroll={handleScroll}
        style={{
          flex: 1,
          backgroundColor: "#000000",
          color: "#00ff00",
          fontFamily: "monospace",
          padding: "1.5rem",
          overflowY: "auto",
          borderRadius: "var(--radius-md)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem", color: "#888" }}>
          <Terminal size={16} /> <span>等待事件…</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          {events.map((event, index) => (
            <div key={`${event.id}-${index}`} style={{ display: "flex", gap: "1rem", wordBreak: "break-all" }}>
              <span style={{ color: "#888", whiteSpace: "nowrap" }}>{new Date(event.created_at).toLocaleTimeString()}</span>
              <span style={{ color: "#ffb86c", whiteSpace: "nowrap", width: "100px", display: "inline-block" }}>[{event.source}]</span>
              <span style={{ color: "#bd93f9", whiteSpace: "nowrap", width: "120px", display: "inline-block" }}>{event.type}</span>
              <span style={{ color: "#f8f8f2" }}>{JSON.stringify(event.payload)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
