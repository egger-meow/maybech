"use client";

import { useCallback, useEffect, useState } from "react";

// Generic "scrub an index forward over time" primitive — not tied to candles or
// reflection specifically, so any future research module that wants a replay/playback
// control over a bounded index range can reuse it.
export function useReplayIndex({
  min,
  max,
  initial,
  stepMs = 700,
}: {
  min: number;
  max: number;
  initial?: number;
  stepMs?: number;
}) {
  const clamp = useCallback((value: number) => Math.min(max, Math.max(min, value)), [min, max]);
  // Raw index is clamped on every read rather than re-synced via an effect, so it always
  // reflects the current [min, max] bounds immediately, even the render after they change.
  const [rawIndex, setRawIndex] = useState(() => initial ?? max);
  const [playing, setPlaying] = useState(false);
  const index = clamp(rawIndex);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setRawIndex((current) => {
        const next = clamp(current) + 1;
        if (next > max) {
          setPlaying(false);
          return current;
        }
        return next;
      });
    }, stepMs);
    return () => clearInterval(timer);
  }, [playing, max, stepMs, clamp]);

  const setIndex = useCallback((value: number) => setRawIndex(value), []);
  const play = useCallback(() => setPlaying(true), []);
  const pause = useCallback(() => setPlaying(false), []);
  const stepForward = useCallback(() => setRawIndex((current) => clamp(current) + 1), [clamp]);
  const stepBackward = useCallback(() => setRawIndex((current) => clamp(current) - 1), [clamp]);
  const reset = useCallback(() => {
    setPlaying(false);
    setRawIndex(max);
  }, [max]);

  // Exposed alongside the clamped `index` so callers can detect when a bound change (not
  // an explicit setIndex/step call) silently pushed the effective index away from the
  // last value the caller actually chose.
  return { index, rawIndex, setIndex, playing, play, pause, stepForward, stepBackward, reset };
}
