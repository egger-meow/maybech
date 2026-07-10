"use client";

import { Pause, Play, RotateCcw, SkipBack, SkipForward } from "lucide-react";

// Generic replay-control UI wired to a lib/research/replay.ts `useReplayIndex` instance.
// Not Adam-Theory-specific: any future "step/replay through history" module reuses it.
export type ReplayToolbarProps = {
  playing: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStepBackward: () => void;
  onStepForward: () => void;
  onReset: () => void;
  stepMs: number;
  onStepMsChange: (stepMs: number) => void;
  disabled?: boolean;
};

const SPEED_OPTIONS = [
  { label: "0.5x", stepMs: 1400 },
  { label: "1x", stepMs: 700 },
  { label: "2x", stepMs: 350 },
  { label: "4x", stepMs: 175 },
];

export default function ReplayToolbar({
  playing,
  onPlay,
  onPause,
  onStepBackward,
  onStepForward,
  onReset,
  stepMs,
  onStepMsChange,
  disabled = false,
}: ReplayToolbarProps) {
  return (
    <div className="replay-toolbar">
      <button type="button" className="btn btn-outline" disabled={disabled} onClick={onStepBackward} aria-label="倒退一根">
        <SkipBack size={16} />
      </button>
      {playing ? (
        <button type="button" className="btn btn-primary" disabled={disabled} onClick={onPause} aria-label="暫停回放">
          <Pause size={16} /> 暫停
        </button>
      ) : (
        <button type="button" className="btn btn-primary" disabled={disabled} onClick={onPlay} aria-label="開始回放">
          <Play size={16} /> 回放
        </button>
      )}
      <button type="button" className="btn btn-outline" disabled={disabled} onClick={onStepForward} aria-label="前進一根">
        <SkipForward size={16} />
      </button>
      <button type="button" className="btn btn-outline" disabled={disabled} onClick={onReset} aria-label="重設至最新">
        <RotateCcw size={16} />
      </button>
      <select value={stepMs} disabled={disabled} onChange={(event) => onStepMsChange(Number(event.target.value))}>
        {SPEED_OPTIONS.map((option) => (
          <option key={option.stepMs} value={option.stepMs}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
