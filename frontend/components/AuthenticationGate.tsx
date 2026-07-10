"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import useSWR from "swr";

import {
  ApiError,
  configureApiToken,
  getLivePreflight,
  getRuntimeCapabilities,
  hasConfiguredApiToken,
} from "@/lib/api";

export default function AuthenticationGate({ children }: { children: ReactNode }) {
  const capabilities = useSWR("runtime-capabilities-public", getRuntimeCapabilities, {
    revalidateOnFocus: true,
  });
  const tokenInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const [authenticated, setAuthenticated] = useState(() => hasConfiguredApiToken());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const requireAuthentication = () => {
      configureApiToken("");
      setAuthenticated(false);
      setError("The API token was rejected. Enter MAYBECH_API_TOKEN again.");
    };
    window.addEventListener("maybech:authentication-required", requireAuthentication);
    return () => {
      window.removeEventListener("maybech:authentication-required", requireAuthentication);
    };
  }, []);

  if (capabilities.error) {
    return (
      <div className="panel error-state">
        Unable to reach the Maybech API. Check that the backend is running.
      </div>
    );
  }

  if (!capabilities.data) {
    return <div className="loading-state">Checking API access...</div>;
  }

  if (!capabilities.data.authentication_required) {
    return children;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current) return;

    const token = tokenInput.current?.value.trim() ?? "";
    if (!token) {
      setError("Enter MAYBECH_API_TOKEN.");
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
          ? "Invalid MAYBECH_API_TOKEN."
          : "Unable to verify the API token. Check the backend logs.",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  if (!authenticated) {
    return (
      <section className="panel" aria-labelledby="authentication-title">
        <h1 id="authentication-title">API Authentication Required</h1>
        <p>
          Enter the backend MAYBECH_API_TOKEN. The token is kept in browser memory
          for this tab and is sent as a bearer token to protected API routes.
        </p>
        <form onSubmit={submit} className="form-grid">
          <label className="field">
            <span>MAYBECH_API_TOKEN</span>
            <input
              ref={tokenInput}
              type="password"
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </label>
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Checking..." : "Unlock Dashboard"}
            </button>
          </div>
        </form>
        {error && <div className="error-state">{error}</div>}
      </section>
    );
  }

  return (
    <>
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-outline"
          onClick={() => {
            configureApiToken("");
            setAuthenticated(false);
            setError("");
          }}
        >
          Lock Dashboard
        </button>
      </div>
      {children}
    </>
  );
}
