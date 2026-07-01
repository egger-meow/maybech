"use client";

import { Search, X } from "lucide-react";
import { useId, useMemo, useState } from "react";

import type { InstrumentMetadataResponse } from "@/lib/api";

type Props = {
  instruments: InstrumentMetadataResponse[];
  value: string[];
  onChange: (value: string[]) => void;
  includeSelf?: boolean;
  multiple?: boolean;
  disabled?: boolean;
  stale?: boolean;
  placeholder?: string;
};

export default function InstrumentSelector({
  instruments,
  value,
  onChange,
  includeSelf = false,
  multiple = false,
  disabled = false,
  stale = false,
  placeholder = "輸入 BTC、ETH 或 USDT 搜尋",
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const optionsId = useId();
  const options = useMemo(() => {
    const all = includeSelf
      ? ["self", ...instruments.map((item) => item.inst_id)]
      : instruments.map((item) => item.inst_id);
    const normalized = query.trim().toUpperCase();
    return all
      .filter((item) => !multiple || !value.includes(item))
      .filter((item) => !normalized || item.toUpperCase().includes(normalized))
      .sort((left, right) => {
        const rank = (item: string) => item === "self" ? 0 : item === "BTC-USDT-SWAP" ? 1 : 2;
        return rank(left) - rank(right) || left.localeCompare(right);
      });
  }, [includeSelf, instruments, multiple, query, value]);

  const select = (instId: string) => {
    onChange(multiple ? [...value, instId] : [instId]);
    setQuery("");
    setOpen(false);
  };

  return (
    <div className="instrument-selector">
      {multiple && value.length > 0 && (
        <div className="instrument-chips">
          {value.map((instId) => (
            <span className="instrument-chip" key={instId}>
              {instId}
              <button type="button" aria-label={`移除 ${instId}`} onClick={() => onChange(value.filter((item) => item !== instId))}>
                <X size={13} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="instrument-combobox">
        <Search size={16} aria-hidden="true" />
        <input
          role="combobox"
          aria-expanded={open}
          aria-controls={optionsId}
          disabled={disabled || stale}
          value={query}
          placeholder={!multiple && value[0] ? value[0] : placeholder}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        />
        {!multiple && value[0] && (
          <button type="button" className="selector-clear" aria-label="清除商品" onMouseDown={(event) => event.preventDefault()} onClick={() => onChange([])}>
            <X size={15} />
          </button>
        )}
        {open && (
          <div id={optionsId} className="instrument-options" role="listbox">
            {options.map((instId) => (
              <button
                type="button"
                role="option"
                aria-selected={value.includes(instId)}
                key={instId}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(instId)}
              >
                <strong>{instId === "self" ? "本商品（self）" : instId}</strong>
                <small>{instId === "self" ? "依目前策略或部位的商品判定" : "OKX 快取中可交易的 SWAP"}</small>
              </button>
            ))}
            {!options.length && <div className="instrument-no-result">沒有符合的可交易商品</div>}
          </div>
        )}
      </div>
      {stale && <div className="inline-warning">OKX 商品資料已過期，更新完成前不能選擇或換算。</div>}
    </div>
  );
}
