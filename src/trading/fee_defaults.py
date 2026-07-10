"""Default modeled OKX SWAP fee rates.

Rates are decimal fractions: 0.0005 means 0.05%.
"""

OKX_MAKER_FEE_RATE = "0.0002"
OKX_TAKER_FEE_RATE = "0.0005"

# Maybech models new entries and closes conservatively as taker fills by default.
DEFAULT_ENTRY_FEE_RATE = OKX_TAKER_FEE_RATE
DEFAULT_EXIT_FEE_RATE = OKX_TAKER_FEE_RATE
