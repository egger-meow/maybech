export function fallbackPricePrecision(value: number | null | undefined): number {
  const absolute = Math.abs(value ?? 0);
  if (absolute >= 1000) return 2;
  if (absolute >= 1) return 4;
  if (absolute >= 0.01) return 6;
  return 8;
}

export function formatPrice(
  value: number | null | undefined,
  precision?: number | null,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const digits = precision ?? fallbackPricePrecision(value);
  return new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: Math.min(digits, 8),
    maximumFractionDigits: Math.min(digits, 8),
    useGrouping: true,
  }).format(value);
}
