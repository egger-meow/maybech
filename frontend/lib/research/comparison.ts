// Generic "how well did a projected path match what actually happened" comparison.
// Aligned index-by-index over whatever overlap exists between the two sequences — not
// specific to reflection, so any future research module projecting a path forward can
// reuse this against real candles once they arrive.
export type ProjectionMatchStats = {
  comparedCount: number;
  directionMatchRate: number | null;
  meanAbsPriceErrorPct: number | null;
};

export function compareProjectionToActual(
  projected: { open: number; close: number }[],
  actual: { open: number; close: number }[],
): ProjectionMatchStats {
  const comparedCount = Math.min(projected.length, actual.length);
  if (comparedCount === 0) {
    return { comparedCount: 0, directionMatchRate: null, meanAbsPriceErrorPct: null };
  }
  let directionMatches = 0;
  let errorSum = 0;
  for (let index = 0; index < comparedCount; index += 1) {
    const p = projected[index];
    const a = actual[index];
    if ((p.close >= p.open) === (a.close >= a.open)) directionMatches += 1;
    if (a.close !== 0) errorSum += Math.abs(p.close - a.close) / Math.abs(a.close);
  }
  return {
    comparedCount,
    directionMatchRate: directionMatches / comparedCount,
    meanAbsPriceErrorPct: (errorSum / comparedCount) * 100,
  };
}
