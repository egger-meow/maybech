"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import useSWR from "swr";

import {
  ApiError,
  configureApiToken,
  getLivePreflight,
  getRuntimeCapabilities,
} from "@/lib/api";

export default function AuthenticationGate({ children }: { children: ReactNode }) {
  const capabilities = useSWR("runtime-capabilities-public", getRuntimeCapabilities, {
    revalidateOnFocus: true,
  });
  const tokenInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const requireAuthentication = () => {
      configureApiToken("");
      setAuthenticated(false);
      setError("驗證已失效，請重新輸入本機操作權杖。");
    };
    window.addEventListener("maybech:authentication-required", requireAuthentication);
    return () => window.removeEventListener(
      "maybech:authentication-required",
      requireAuthentication,
    );
  }, []);

  if (capabilities.error) {
    return <div className="panel error-state">無法讀取公開執行能力；請確認 API 已啟動且網址正確。</div>;
  }
  if (!capabilities.data) {
    return <div className="loading-state">正在確認儀表板驗證需求…</div>;
  }
  if (!capabilities.data.authentication_required) {
    return children;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current) return;
    const token = tokenInput.current?.value.trim() ?? "";
    if (!token) {
      setError("請輸入 MAYBECH_API_TOKEN。");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    configureApiToken(token);
    if (tokenInput.current) tokenInput.current.value = "";
    try {
      await getLivePreflight();
      setAuthenticated(true);
    } catch (caught) {
      configureApiToken("");
      setAuthenticated(false);
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "操作權杖不正確。"
          : "驗證 API 無法使用，尚未開放受保護頁面。",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  if (!authenticated) {
    return (
      <section className="panel" aria-labelledby="authentication-title">
        <h1 id="authentication-title">需要本機操作驗證</h1>
        <p>權杖只保留在目前分頁記憶體，重新整理後必須再次輸入；不會寫入 localStorage、網址或日誌。</p>
        <form onSubmit={submit} className="form-grid">
          <label className="field">
            <span>MAYBECH_API_TOKEN</span>
            <input ref={tokenInput} type="password" autoComplete="off" spellCheck={false} disabled={busy} />
          </label>
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "驗證中…" : "驗證並開啟儀表板"}</button>
          </div>
        </form>
        {error && <div className="error-state">{error}</div>}
      </section>
    );
  }

  return (
    <>
      <div className="form-actions">
        <button type="button" className="btn btn-outline" onClick={() => {
          configureApiToken("");
          setAuthenticated(false);
          setError("");
        }}>登出本機操作權杖</button>
      </div>
      {children}
    </>
  );
}
