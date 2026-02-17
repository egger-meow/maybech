# Maybech 🌊

**Advanced Crypto Trading TUI Framework**

Maybech is a high-performance, automated trading system built for the modern crypto trader. It combines quantitative strategy development, rigorous backtesting, and real-time execution into a sleek, terminal-based interface.

![Maybech Dashboard](docs/imgs/panel.png)

## 🎯 Project Vision
This repository represents a commitment to technical excellence in quantitative trading. My goal is to bridge the gap between complex market data and actionable strategies, evolving into a **robust crypto trader** who leverages automation to eliminate emotional bias and maximize efficiency.

---

## 🚀 Key Features

### 1. Auto-Strategy Executor
*   **Live Monitoring**: Real-time tracking of strategy status, signal generation, and trade execution.
*   **Status Indicator**: Visual live indicator (● RUNNING, ○ STALE, OFFLINE) with polling progress tracking.
*   **Live Logs**: Instant visibility into recent signals and OKX API interactions.

### 2. Backtest Engine
*   **High-Fidelity Simulation**: Validates strategies against historical OHLCV data.
*   **Dynamic Logic**: Implements 1:1 Risk/Reward validation with conservative outcome handling (SL priority in volatile candles).
*   **Comprehensive Metrics**: Tracks Win Rate, PnL, Profit Factor, and trade-by-trade breakdowns.

### 3. Grid Search Optimizer
*   **Parameter Discovery**: Multi-dimensional search across parameter ranges (K-Long, K-Short, Gap Threshold).
*   **Progressive UI**: Real-time progress bar and status updates directly within the TUI.
*   **Scoring System**: Automatically ranks configurations based on historical performance.

### 4. Smart Data Caching
*   **Incremental Fetching**: Intelligently identifies missing data gaps and fetches only what's needed from OKX. 
*   **In-Memory Performance**: Serves repeated backtests instantly from a persistent cache, significantly reducing API latency and respecting rate limits.
*   **Preloading Logic**: Automatically warms up the cache for all trading pairs on application startup.

### 5. Multi-Channel Notifications
*   **Price Alerts**: Stay informed on the go with integrated **Line Bot** and **Email** notifications for critical price points and trade events.

---

## 🛠 Tech Stack
*   **Core**: Python 3.14 (Optimized for modern async workloads)
*   **TUI**: [Textual](https://github.com/Textualize/textual) (CSS-driven modern terminal interface)
*   **Analysis**: Pandas, NumPy, TA-Lib (Technical analysis library)
*   **Exchange**: [OKX API](https://www.okx.com/docs-v5)
*   **Package Management**: [uv](https://github.com/astral-sh/uv) (Extremely fast Python package installer and resolver)

---

## 📦 Installation & Setup

Ensure you have `uv` installed. If not, get it from [astral.sh/uv](https://astral.sh/uv).

1.  **Clone the Repo**:
    ```bash
    git clone https://github.com/egger-meow/maybech.git
    cd maybech
    ```

2.  **Setup Environment**:
    ```bash
    uv venv
    source .venv/Scripts/activate # On Windows
    uv pip install -r requirements.txt
    ```

3.  **Configuration**:
    Create a `.env` file based on `.env.example` and add your OKX API credentials.

4.  **Launch the App**:
    ```bash
    python main.py
    ```

---

*Developed with precision for the future of decentralized finance.*
