"use client";

import { useCallback, useEffect, useState } from "react";
import { BellRing, CheckCircle2, Mail, MessageCircle, RefreshCw, ShieldAlert } from "lucide-react";

import {
  getNotificationHealth,
  sendNotificationTest,
  type NotificationChannelHealthResponse,
  type NotificationHealthResponse,
} from "@/lib/api";

const stateCopy: Record<NotificationChannelHealthResponse["state"], { label: string; tone: string; detail: string }> = {
  disabled: { label: "尚未設定", tone: "muted", detail: "後端環境變數不完整，系統不會嘗試投遞。" },
  unverified: { label: "尚未驗證", tone: "warning", detail: "設定存在，但尚無成功投遞證據。" },
  healthy: { label: "投遞正常", tone: "success", detail: "最近一次投遞已由傳輸端成功接受。" },
  failing: { label: "投遞失敗", tone: "danger", detail: "最近投遞失敗，事件仍留在佇列等待重試。" },
  backoff: { label: "退避重試中", tone: "danger", detail: "系統已暫緩重試，避免持續轟炸失效通道。" },
};

const formatTime = (value?: string | null) => value
  ? new Date(value).toLocaleString("zh-TW")
  : "沒有資料";

export default function NotificationManagementPage() {
  const [health, setHealth] = useState<NotificationHealthResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<"line" | "email" | null>(null);
  const [confirmed, setConfirmed] = useState<Record<"line" | "email", boolean>>({ line: false, email: false });
  const [result, setResult] = useState("");

  const refresh = useCallback(async () => {
    try {
      const next = await getNotificationHealth();
      setHealth(next);
      setError("");
    } catch {
      setError("無法取得通知健康狀態；請勿假設 LINE 或 Gmail 正在正常運作。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const runTest = async (channel: "line" | "email") => {
    setTesting(channel);
    setResult("");
    try {
      const response = await sendNotificationTest({ confirm: true, channel });
      setResult(response.success
        ? `${channel === "line" ? "LINE" : "Gmail"} 測試通知已成功送出。`
        : `${channel === "line" ? "LINE" : "Gmail"} 測試失敗：${response.error ?? "傳輸端未接受"}`);
      setConfirmed((current) => ({ ...current, [channel]: false }));
      await refresh();
    } catch {
      setResult("測試要求未完成。請檢查後端設定與事件稽核，不要把按下按鈕視為送達。");
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <h1>通知管理</h1>
          <p>檢查 LINE 與 Gmail 是否真的可用、是否有失敗退避，以及生命週期事件是否正在排隊。</p>
        </div>
        <button className="btn btn-outline" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw size={17} /> 重新整理
        </button>
      </header>

      {error && <div className="error-state"><ShieldAlert size={18} /> {error}</div>}
      {loading && !health && <div className="loading-state">正在讀取通知健康證據…</div>}

      {health && (
        <>
          <section className={`notification-summary ${health.backlog_count > 0 ? "has-backlog" : ""}`}>
            <div><BellRing size={24} /><strong>生命週期通知服務</strong></div>
            <span className={`badge ${health.service_enabled ? "success" : "danger"}`}>
              {health.service_enabled ? "服務已啟用" : "服務未啟用"}
            </span>
            <p>待處理事件 <strong>{health.backlog_count}</strong> 筆</p>
            <small>後端檢查時間：{formatTime(health.checked_at)}</small>
          </section>

          <section className="notification-grid">
            {health.channels.map((channel) => {
              const copy = stateCopy[channel.state];
              const isLine = channel.channel === "line";
              return (
                <article className="panel notification-card" key={channel.channel}>
                  <div className="notification-channel-head">
                    <span className="notification-icon">{isLine ? <MessageCircle /> : <Mail />}</span>
                    <div><h2>{isLine ? "LINE" : "Gmail"}</h2><p>{copy.detail}</p></div>
                    <span className={`badge ${copy.tone}`}>{copy.label}</span>
                  </div>

                  <dl className="notification-facts">
                    <div><dt>設定狀態</dt><dd>{channel.configured ? "必要欄位已在後端設定" : "缺少必要後端設定"}</dd></div>
                    <div><dt>最近成功</dt><dd>{formatTime(channel.last_success_at)}</dd></div>
                    <div><dt>最近失敗</dt><dd>{formatTime(channel.last_failure_at)}</dd></div>
                    <div><dt>連續失敗</dt><dd>{channel.consecutive_failures} 次</dd></div>
                    <div><dt>下次重試</dt><dd>{formatTime(channel.next_retry_at)}</dd></div>
                    <div><dt>失敗類型</dt><dd className="mono">{channel.last_error ?? "沒有資料"}</dd></div>
                  </dl>

                  <div className="notification-test-zone">
                    <strong>送出實際測試通知</strong>
                    <p>這會真的呼叫已設定的通道，但不會放置委託，也不會顯示任何憑證。</p>
                    <label className="confirm-check">
                      <input
                        type="checkbox"
                        checked={confirmed[channel.channel]}
                        onChange={(event) => setConfirmed((current) => ({
                          ...current,
                          [channel.channel]: event.target.checked,
                        }))}
                      />
                      我確認要傳送一則 Maybech 測試通知
                    </label>
                    <button
                      className="btn btn-primary"
                      disabled={!channel.configured || !confirmed[channel.channel] || testing !== null}
                      onClick={() => void runTest(channel.channel)}
                    >
                      <CheckCircle2 size={17} />
                      {testing === channel.channel ? "正在測試…" : `測試 ${isLine ? "LINE" : "Gmail"}`}
                    </button>
                  </div>
                </article>
              );
            })}
          </section>
        </>
      )}

      {result && <div className="panel notification-result" role="status">{result}</div>}
      <section className="panel notification-security-note">
        <ShieldAlert size={22} />
        <div><strong>憑證只留在後端環境</strong><p>此頁只顯示是否設定與投遞證據，不會回傳 Token、密碼、LINE User ID 或收件地址。修改憑證後請重新啟動執行環境，再從此頁明確測試。</p></div>
      </section>
    </div>
  );
}
