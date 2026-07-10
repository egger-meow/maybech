// Adam Theory secondary reflection is a 180-degree point reflection: every historical
// candle is mirrored across a single center point in both time and price at once (not
// time and price independently), so a candle just before the center maps to a candle
// just after it, and a candle far before the center maps far into the future.
export type ReflectableCandle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type ReflectedCandle = ReflectableCandle;

export function reflectPoint(
  timestampMs: number,
  price: number,
  centerTimestampMs: number,
  centerPrice: number,
): { timestampMs: number; price: number } {
  return {
    timestampMs: 2 * centerTimestampMs - timestampMs,
    price: 2 * centerPrice - price,
  };
}

// Reflecting a candle swaps high/low: the point reflection of the lowest price of a
// candle becomes the highest price of its mirror image (and vice versa), which is what
// keeps the mirrored candle's body/wick geometry an exact reflection rather than a
// distorted one.
export function reflectCandle(
  candle: ReflectableCandle,
  centerTimestampMs: number,
  centerPrice: number,
): ReflectedCandle {
  const timestampMs = new Date(candle.timestamp).getTime();
  return {
    timestamp: new Date(2 * centerTimestampMs - timestampMs).toISOString(),
    open: 2 * centerPrice - candle.open,
    close: 2 * centerPrice - candle.close,
    high: 2 * centerPrice - candle.low,
    low: 2 * centerPrice - candle.high,
  };
}

export type ReflectionResult = {
  source: ReflectableCandle[];
  reflected: ReflectedCandle[];
  centerTimestamp: string | null;
  centerPrice: number | null;
};

// centerIndex's own candle is the reflection point itself (reflecting it onto itself is
// the identity transform), so the reflected series is built from the `count` candles
// strictly before it. Reflection price is the center candle's close: picking a candle as
// the center most naturally reads as "mirror around what this candle closed at."
export function buildReflection(
  candles: ReflectableCandle[],
  centerIndex: number,
  count: number,
): ReflectionResult {
  if (centerIndex < 0 || centerIndex >= candles.length || count <= 0) {
    return { source: [], reflected: [], centerTimestamp: null, centerPrice: null };
  }
  const center = candles[centerIndex];
  const centerTimestampMs = new Date(center.timestamp).getTime();
  const centerPrice = center.close;
  const start = Math.max(0, centerIndex - count);
  const source = candles.slice(start, centerIndex);
  const reflected = source
    .map((candle) => reflectCandle(candle, centerTimestampMs, centerPrice))
    .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  return { source, reflected, centerTimestamp: center.timestamp, centerPrice };
}
