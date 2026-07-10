// Generic candle-array selection helpers: picking a reference ("center") candle out of an
// already-fetched array, and reading the real candles that follow it. Deliberately
// decoupled from any reflection-specific logic so future research modules can reuse it
// for their own "pick a reference candle" needs.

export function clampCenterIndex(length: number, index: number, minHistory: number): number {
  if (length <= 0) return 0;
  const min = Math.min(Math.max(minHistory, 0), length - 1);
  const max = length - 1;
  return Math.min(max, Math.max(min, index));
}

export function selectActualFollowing<T>(candles: T[], centerIndex: number, count: number): T[] {
  if (centerIndex < 0 || centerIndex >= candles.length || count <= 0) return [];
  return candles.slice(centerIndex + 1, centerIndex + 1 + count);
}
