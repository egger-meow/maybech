"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";
import { Terminal, Play, Square } from "lucide-react";

type RuntimeEvent = {
  id: string;
  type: string;
  source: string;
  created_at: string;
  payload: Record<string, unknown>;
};

export default function Events() {
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const eventsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/events?limit=50`)
      .then((res) => res.json() as Promise<RuntimeEvent[]>)
      .then((data) => {
        setEvents(data.reverse());
      })
      .catch((error: unknown) => {
        console.error("Failed to fetch events", error);
      });

    const ws = new WebSocket(`${API_BASE.replace(/^http/, "ws")}/ws/events`);
    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onmessage = (msg) => {
      if (isPaused) return;
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
  }, [isPaused]);

  useEffect(() => {
    if (!isPaused && eventsEndRef.current) {
      eventsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, isPaused]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem", height: "100%" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "1rem" }}>
        <div>
          <h1 style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "0.5rem" }}>Events Trace</h1>
          <p style={{ color: "var(--text-muted)" }}>Live daemon events from the runtime API.</p>
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
            {isConnected ? "Connected" : "Disconnected"}
          </div>
          <button
            className={`btn ${isPaused ? "btn-primary" : "btn-outline"}`}
            onClick={() => setIsPaused(!isPaused)}
            style={{ padding: "0.5rem 1rem" }}
          >
            {isPaused ? <Play size={16} /> : <Square size={16} />}
            {isPaused ? "Resume" : "Pause"}
          </button>
        </div>
      </header>

      <div
        className="glass-panel"
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
          <Terminal size={16} /> <span>Waiting for events...</span>
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
