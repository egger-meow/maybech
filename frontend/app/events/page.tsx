"use client";

import { useEffect, useRef, useState } from "react";
import { listRecentEvents, wsUrl, type RuntimeEvent } from "@/lib/api";
import { Terminal, Play, Square } from "lucide-react";

export default function Events() {
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isAutoScroll, setIsAutoScroll] = useState(true);
  const eventsEndRef = useRef<HTMLDivElement>(null);
  const isPausedRef = useRef(isPaused);

  useEffect(() => {
    isPausedRef.current = isPaused;
  }, [isPaused]);

  useEffect(() => {
    listRecentEvents(50)
      .then((data) => {
        setEvents(data.reverse());
      })
      .catch((error: unknown) => {
        console.error("Failed to fetch events", error);
      });

    const ws = new WebSocket(wsUrl("/ws/events"));
    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onmessage = (msg) => {
      if (isPausedRef.current) return;
      try {
        const data = JSON.parse(msg.data) as RuntimeEvent;
        setEvents((prev) => [...prev, data].slice(-200));
      } catch (error: unknown) {
        console.error("Failed to parse runtime event", error);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    if (!isPaused && isAutoScroll && eventsEndRef.current) {
      eventsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, isPaused, isAutoScroll]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setIsAutoScroll(isAtBottom);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem", height: "100%" }}>
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

      <div
        className="glass-panel"
        onScroll={handleScroll}
        style={{
          flex: 1,
          backgroundColor: "#000000",
          color: "#00ff00",
          fontFamily: "monospace",
          padding: "1.5rem",
          overflowY: "auto",
          borderRadius: "var(--radius-md)",
          minHeight: "60vh",
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
          <div ref={eventsEndRef} />
        </div>
      </div>
    </div>
  );
}
