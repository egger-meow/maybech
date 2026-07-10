export const OKX_MAKER_FEE_RATE = 0.0002;
export const OKX_TAKER_FEE_RATE = 0.0005;

// Default risk modeling is conservative: assume taker on both open and close.
export const DEFAULT_ENTRY_FEE_RATE = OKX_TAKER_FEE_RATE;
export const DEFAULT_EXIT_FEE_RATE = OKX_TAKER_FEE_RATE;
export const DEFAULT_ENTRY_FEE_PERCENT = String(DEFAULT_ENTRY_FEE_RATE * 100);
export const DEFAULT_EXIT_FEE_PERCENT = String(DEFAULT_EXIT_FEE_RATE * 100);
