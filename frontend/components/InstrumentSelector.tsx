"use client";

import { Search, X } from "lucide-react";
import { useId, useMemo, useState, type KeyboardEvent } from "react";

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
  eligibleInstruments?: string[];
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
  eligibleInstruments,
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
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
        const preferred = ["self", "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP"];
        const rank = (item: string) => {
          const index = preferred.indexOf(item);
          return index === -1 ? preferred.length : index;
        };
        return rank(left) - rank(right) || left.localeCompare(right);
      });
  }, [includeSelf, instruments, multiple, query, value]);

  const select = (instId: string) => {
    onChange(multiple ? [...value, instId] : [instId]);
    setQuery("");
    setOpen(false);
  };

  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.min(current + 1, Math.max(0, options.length - 1)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.max(0, current - 1));
    } else if (event.key === "Enter" && open && options[activeIndex]) {
      event.preventDefault();
      select(options[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
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
          aria-activedescendant={open && options[activeIndex] ? `${optionsId}-${activeIndex}` : undefined}
          disabled={disabled || stale}
          value={query}
          placeholder={!multiple && value[0] ? value[0] : placeholder}
          onFocus={() => { setOpen(true); setActiveIndex(0); }}
          onBlur={() => setOpen(false)}
          onChange={(event) => { setQuery(event.target.value); setOpen(true); setActiveIndex(0); }}
          onKeyDown={keyDown}
        />
        {!multiple && value[0] && (
          <button type="button" className="selector-clear" aria-label="清除商品" onMouseDown={(event) => event.preventDefault()} onClick={() => onChange([])}>
            <X size={15} />
          </button>
        )}
        {open && (
          <div id={optionsId} className="instrument-options" role="listbox">
            {options.map((instId, index) => (
              <button
                type="button"
                role="option"
                id={`${optionsId}-${index}`}
                aria-selected={value.includes(instId)}
                key={instId}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => select(instId)}
                className={activeIndex === index ? "active" : undefined}
              >
                <strong>{instId === "self" ? "本商品（self）" : instId}</strong>
                <small>{instId === "self" ? "依目前策略或部位的商品判定" : eligibleInstruments ? eligibleInstruments.includes(instId) ? "帳戶風險邊界允許 · 可交易 SWAP" : "不在帳戶 allowlist · 可先研究或儲存，啟用時會封鎖" : "快取中可交易的 SWAP"}</small>
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
