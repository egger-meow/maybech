# Maybech

Crypto auto-trader focused on **short-term momentum** and **backtesting validation**, built on the OKX API.

## Quickstart

```bash
# 1. Clone & enter
git clone <repo-url> && cd maybech

# 2. Create virtualenv
python -m venv .venv && .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env   # then fill in your OKX keys

# 5. Run (demo mode by default)
python main.py
```

## Architecture

```
src/
├── config/        — Settings & .env loading
├── exchange/      — OKX REST + WebSocket wrappers
├── data/          — Candlestick ingestion & indicators
├── strategies/    — Signal generators (momentum, etc.)
├── backtesting/   — Backtest engine & reports
├── trading/       — Order execution & risk management
├── monitor/       — Account balance & position tracking
├── notifications/ — LINE Bot & email alerts
└── utils/         — Logging & helpers
```

## Key Concepts

1. **Momentum detection** — detects volume spikes on 15m candles, enters long/short instantly
2. **Backtest-before-live** — no strategy goes live unless it passes win-rate & return-rate thresholds
3. **Bear-market bias** — asymmetric stop-loss (tighter for longs, wider for shorts)
4. **Live monitoring** — tracks performance in real-time, auto-stops underperforming strategies
