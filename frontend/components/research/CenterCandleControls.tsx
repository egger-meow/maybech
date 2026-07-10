"use client";

// Generic "pick a reference candle out of an already-fetched array" control: a slider
// plus step/jump buttons. Decoupled from any specific research module so future modules
// that need to pick a reference candle (not just Adam Theory reflection) can reuse it.
export type CenterCandleControlsProps = {
  minIndex: number;
  maxIndex: number;
  index: number;
  onChange: (index: number) => void;
  label: string;
  disabled?: boolean;
};

export default function CenterCandleControls({
  minIndex,
  maxIndex,
  index,
  onChange,
  label,
  disabled = false,
}: CenterCandleControlsProps) {
  const clampedIndex = Math.min(maxIndex, Math.max(minIndex, index));
  const atLatest = clampedIndex >= maxIndex;
  const atEarliest = clampedIndex <= minIndex;
  return (
    <div className="center-candle-controls">
      <div className="center-candle-controls-row">
        <button
          type="button"
          className="btn btn-outline"
          disabled={disabled || atEarliest}
          onClick={() => onChange(clampedIndex - 1)}
        >
          上一根
        </button>
        <input
          type="range"
          min={minIndex}
          max={maxIndex}
          value={clampedIndex}
          disabled={disabled || minIndex >= maxIndex}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <button
          type="button"
          className="btn btn-outline"
          disabled={disabled || atLatest}
          onClick={() => onChange(clampedIndex + 1)}
        >
          下一根
        </button>
        <button type="button" className="btn btn-outline" disabled={disabled || atLatest} onClick={() => onChange(maxIndex)}>
          跳至最新
        </button>
      </div>
      <p className="center-candle-controls-label">中心 K 棒：{label}</p>
    </div>
  );
}
