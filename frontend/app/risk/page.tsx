"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, Save, ShieldCheck } from "lucide-react";

import InstrumentSelector from "@/components/InstrumentSelector";
import {
  ApiError,
  bootstrapSimulationInstruments,
  getEntryControl,
  getLivePreflight,
  getRiskLimits,
  listInstruments,
  updateRiskLimits,
  type AccountRiskLimitsResponse,
  type InstrumentMetadataListResponse,
} from "@/lib/api";

type FormState = {
  enabled: boolean;
  maxOrder: string;
  maxExposure: string;
  maxLeverage: string;
  allowedInstruments: string[];
};

const emptyForm: FormState = {
  enabled: false,
  maxOrder: "",
  maxExposure: "",
  maxLeverage: "",
  allowedInstruments: [],
};

const fromLimits = (limits: AccountRiskLimitsResponse): FormState => ({
  enabled: limits.enabled,
  maxOrder: String(limits.max_order_notional_usd),
  maxExposure: String(limits.max_total_exposure_usd),
  maxLeverage: String(limits.max_leverage),
  allowedInstruments: limits.allowed_instruments ?? [],
});

export default function RiskLimitsPage() {
  const [saved, setSaved] = useState<AccountRiskLimitsResponse | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [entriesEnabled, setEntriesEnabled] = useState<boolean | null>(null);
  const [instruments, setInstruments] = useState<InstrumentMetadataListResponse | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [simulationMode, setSimulationMode] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [riskResult, entryResult, instrumentResult, preflightResult] = await Promise.allSettled([
        getRiskLimits(),
        getEntryControl(),
        listInstruments(),
        getLivePreflight(),
      ]);
      setSimulationMode(
        preflightResult.status === "fulfilled"
        && preflightResult.value.execution_mode === "simulation"
      );
      if (entryResult.status === "fulfilled") {
        setEntriesEnabled(entryResult.value.entries_enabled);
      } else {
        setEntriesEnabled(null);
      }
      setInstruments(instrumentResult.status === "fulfilled" ? instrumentResult.value : null);
      if (riskResult.status === "fulfilled") {
        setSaved(riskResult.value);
        setForm(fromLimits(riskResult.value));
        setError("");
      } else if (riskResult.reason instanceof ApiError && riskResult.reason.status === 404) {
        setSaved(null);
        setForm(emptyForm);
        setError("");
      } else {
        setError("無法讀取 /risk/limits；目前數值未知，請勿假設實盤風險上限已設定。");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const bootstrapCatalog = async () => {
    setError("");
    try {
      const result = await bootstrapSimulationInstruments();
      setInstruments(result);
      setNotice("已建立本機 Simulation 商品資料；此動作未連接 OKX，切換 Demo／Live 時會強制由交易所覆寫。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Simulation 商品資料建立失敗。");
    }
  };

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  const dirty = useMemo(() => {
    const baseline = saved ? fromLimits(saved) : emptyForm;
    return JSON.stringify(form) !== JSON.stringify(baseline);
  }, [form, saved]);

  const validation = useMemo(() => {
    const order = Number(form.maxOrder);
    const exposure = Number(form.maxExposure);
    const leverage = Number(form.maxLeverage);
    if (![order, exposure, leverage].every((value) => Number.isFinite(value) && value > 0)) {
      return "三個數值都必須是大於 0 的有效數字。";
    }
    if (order > exposure) return "單筆委託上限不可高於帳戶總曝險上限。";
    if (leverage > 125) return "最大槓桿不可高於 125 倍。";
    if (form.enabled && form.allowedInstruments.length === 0) return "啟用風險信封前，至少選擇一個允許進場的 SWAP。";
    return "";
  }, [form]);

  const save = async () => {
    if (validation || !confirmed || entriesEnabled !== false) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await updateRiskLimits({
        confirm: true,
        expected_updated_at: saved?.updated_at ?? null,
        enabled: form.enabled,
        max_order_notional_usd: Number(form.maxOrder),
        max_total_exposure_usd: Number(form.maxExposure),
        max_leverage: Number(form.maxLeverage),
        allowed_instruments: form.allowedInstruments,
      });
      setSaved(next);
      setForm(fromLimits(next));
      setConfirmed(false);
      setNotice("風險上限已儲存，before／after 稽核證據也已寫入。此動作不會開啟進場閘門。");
    } catch (reason) {
      const info = reason instanceof ApiError && typeof reason.info === "object" && reason.info
        ? reason.info as { detail?: string | { message?: string; current_updated_at?: string; strategies?: { strategy_name?: string; instruments?: string[] }[] } }
        : null;
      if (reason instanceof ApiError && reason.status === 409 && typeof info?.detail === "object" && info.detail.current_updated_at) {
        const current = info.detail.current_updated_at
          ? new Date(info.detail.current_updated_at).toLocaleString("zh-TW")
          : "未知";
        setError(`後端風險上限已被其他工作階段更新（${current}）。目前表單保留未儲存內容；請重新讀取並逐項核對後再送出。`);
      } else if (reason instanceof ApiError && reason.status === 409 && typeof info?.detail === "object" && info.detail.strategies?.length) {
        const conflicts = info.detail.strategies.map((item) => `${item.strategy_name ?? "未命名策略"}（${item.instruments?.join("、") ?? "未知商品"}）`).join("；");
        setError(`不能移除仍由已啟用策略使用的商品：${conflicts}。請先停用或修改這些策略。`);
      } else {
        const detail = typeof info?.detail === "string" ? info.detail : "";
        setError(detail || "儲存失敗；畫面保留未儲存內容，後端原值未被假設為已更新。");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-stack">
      <header className="page-header">
        <div><h1>帳戶風險上限</h1><p>設定每筆委託、總曝險與槓桿的硬上限。這些值只限制新進場，不會自行武裝或啟用策略。</p></div>
        <button className="btn btn-outline" onClick={() => void load()} disabled={loading || saving}><RefreshCw size={17} />重新讀取</button>
      </header>

      <section className={`risk-state-banner ${saved?.enabled ? "enabled" : "disabled"}`}>
        <ShieldCheck size={25} />
        <div><strong>{saved ? saved.enabled ? "風險信封已啟用" : "風險信封已停用" : "尚未建立風險信封"}</strong><p>{saved ? `後端更新：${new Date(saved.updated_at).toLocaleString("zh-TW")}` : "Demo／Live Armed preflight 會封鎖；Simulation 仍可使用。"}</p></div>
        <span className={`badge ${entriesEnabled === false ? "success" : "danger"}`}>{entriesEnabled === false ? "進場已停用，可編輯" : entriesEnabled === true ? "進場已啟用，禁止編輯" : "進場狀態未知"}</span>
      </section>

      {error && <div className="error-state"><AlertTriangle size={18} />{error}</div>}
      {notice && <div className="saved-confirmation"><CheckCircle2 size={18} />{notice}</div>}
      {loading ? <div className="loading-state">正在讀取持久化風險上限…</div> : (
        <section className="panel risk-editor">
          <div className="panel-heading"><div><h2>安全信封</h2><p>提高任何數值都可能放大真實資金曝險；儲存前必須先停用進場並逐項確認。</p></div><span className={dirty ? "dirty-note" : "saved-note"}>{dirty ? "有未儲存變更" : saved ? "已與後端同步" : "尚未建立"}</span></div>

          <div className="risk-limit-grid">
            <label className="field"><span>單筆委託名目上限</span><span className="risk-input"><input inputMode="decimal" value={form.maxOrder} onChange={(event) => setForm({ ...form, maxOrder: event.target.value })} placeholder="例如 100" /><b>USDT</b></span><small>每一次新進場最多可使用的估算名目金額。</small></label>
            <label className="field"><span>帳戶總曝險上限</span><span className="risk-input"><input inputMode="decimal" value={form.maxExposure} onChange={(event) => setForm({ ...form, maxExposure: event.target.value })} placeholder="例如 500" /><b>USDT</b></span><small>現有部位、未成交進場與新委託合計不得超過此值。</small></label>
            <label className="field"><span>最大 Cross 槓桿</span><span className="risk-input"><input inputMode="decimal" value={form.maxLeverage} onChange={(event) => setForm({ ...form, maxLeverage: event.target.value })} placeholder="例如 5" /><b>倍</b></span><small>OKX 回報槓桿高於此值時，新進場會被封鎖。</small></label>
          </div>

          <div className="risk-allowlist">
            <div><strong>允許進場商品</strong><p>這是帳戶層級最後一道邊界。策略即使誤選其他商品，preflight 與每筆 entry approval 都會封鎖。</p></div>
            <InstrumentSelector
              instruments={instruments?.items ?? []}
              value={form.allowedInstruments}
              onChange={(allowedInstruments) => setForm({ ...form, allowedInstruments })}
              multiple
              stale={!instruments || instruments.stale}
              placeholder="輸入 BTC、ETH 或 USDT 搜尋允許商品"
            />
            {!instruments && <div className="error-state">商品快取尚未建立；不提供未標示來源的假選項。{simulationMode && <button type="button" className="btn btn-outline" onClick={() => void bootstrapCatalog()}>建立 Simulation 商品資料</button>}</div>}
          </div>

          <label className="risk-enable-toggle"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span><strong>啟用此風險信封</strong><small>實盤啟動要求此項已啟用；它不等於允許送單。</small></span></label>

          {validation && <div className="inline-warning">{validation}</div>}
          {entriesEnabled !== false && <div className="danger-zone"><strong>目前不可儲存</strong><p>{entriesEnabled ? "請先使用進場 Kill Switch 停用新進場，再修改安全上限。" : "無法確認進場閘門已停用；為避免競態，後端會拒絕修改。"}</p></div>}

          <div className="risk-confirm-zone">
            <AlertTriangle size={22} />
            <div><strong>這是高風險設定變更</strong><p>確認數值、單位與槓桿後再儲存。後端會原子寫入新值與 before／after 稽核。</p></div>
            <label className="confirm-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我已檢查三個上限，確認要覆寫後端設定</label>
            <button className="btn btn-danger" onClick={() => void save()} disabled={!dirty || Boolean(validation) || !confirmed || entriesEnabled !== false || saving}><Save size={17} />{saving ? "正在儲存…" : "確認並儲存風險上限"}</button>
          </div>
        </section>
      )}
    </div>
  );
}
