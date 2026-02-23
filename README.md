<div align="center">

# 🌊 Maybech

**The Next-Generation Automated Crypto Trading & Market Tracking Framework**

[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![Textual](https://img.shields.io/badge/TUI-Textual-brightgreen)](https://textual.textualize.io/)
[![OKX](https://img.shields.io/badge/Exchange-OKX-black)](https://www.okx.com/)

*A sleek, high-performance command center built for the modern quantitative trader.*

</div>

---

## 🎯 Project Vision

Maybech is more than just a trading bot—it's a commitment to technical excellence in quantitative finance. Built to bridge the gap between complex market data and actionable, automated strategies, it stands as a robust showcase of software engineering and market analysis.

Whether you're tracking violent market swings instantly, developing fluent trading strategies, or backtesting ideas, Maybech brings it all together under a graceful, terminal-based interface (TUI). It is designed to eliminate emotional bias, maximize execution efficiency, and bring enterprise-grade market awareness directly to your terminal.

![Maybech Dashboard](docs/imgs/panel.png)

---

## ✨ Core Highlights

### 🕹️ Graceful Terminal UI (TUI)
Manage the chaos of the crypto markets with elegance. Powered by **Textual**, the TUI provides a visual, real-time command center:
- **Live Strategy Monitoring**: Track auto-execution status (● RUNNING, ○ STALE) with visual polling indicators.
- **Progressive UI**: See real-time metrics, backtest progress bars, and streaming system logs directly in your terminal without ever leaving the keyboard.

### ⚡ Instant Market Tracking & AutoTrader
Never miss a beat in the market.
- **Fluent Strategy Development**: Craft complex quantitative strategies leveraging Pandas and TA-Lib with ease.
- **Auto-Strategy Executor**: Deploy your logic into the live market. The engine handles real-time signal generation, 1:1 Risk/Reward validation, conservative outcome handling, and zero-hesitation execution via the OKX API.

### 📡 Real-Time Intelligent Notifications
Stay connected, no matter where you are.
- **Instant Alerts**: Receive immediate **LINE Bot** pushes (and soon Email) whenever the market experiences severe fluctuations or critical price proximity events.
- **Actionable Intel**: Get notified of instant trade entries, take-profits, and stop-losses directly to your mobile device, in beautifully formatted Traditional Chinese.

### 🔬 High-Fidelity Backtest Engine & Optimizer
Prove it before you trade it.
- **Rigorous Simulation**: Validate strategies against comprehensive historical OHLCV data with precision.
- **Grid Search Optimizer**: Run multi-dimensional parameter discovery (e.g., K-Long, K-Short, Gap Thresholds) with automated scoring and ranking to find the absolute mathematically optimal configuration.
- **Smart Data Caching**: Intelligent incremental fetching perfectly respects API rate limits while serving repeated backtests instantly from persistent cache.

---

## 🛠 Tech Stack

*   **Core**: Python 3.14 (Optimized for modern async workloads & performance)
*   **User Interface**: [Textual](https://github.com/Textualize/textual) for a reactive, CSS-driven modern terminal experience
*   **Data & Analysis**: Pandas, NumPy, TA-Lib for lightning-fast quantitative analysis
*   **Exchange Layer**: [OKX API v5](https://www.okx.com/docs-v5)
*   **Ecosystem**: [uv](https://github.com/astral-sh/uv) (Extremely fast Python package installer and virtual environment resolver)

---

## 📦 Installation & Setup

We strictly use `uv` for blazing-fast project management. Get it from [astral.sh/uv](https://astral.sh/uv).

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/egger-meow/maybech.git
    cd maybech
    ```

2.  **Setup Environment (Strictly via UV)**:
    ```bash
    # Embody the standard
    uv venv
    
    # Activate (Windows)
    .venv\Scripts\activate 
    
    # Install dependencies instantly
    uv pip install -r requirements.txt
    ```

3.  **Configuration**:
    Create a `.env` file based on `.env.example`. Add your OKX API credentials and LINE Messaging API tokens.
    Customize parameters for market trackers in `src/config/notificator_config.json`.

4.  **Launch the Command Center**:
    ```bash
    uv run python run_services.py
    ```

---

<div align="center">
<i>Developed with precision for the future of decentralized finance. Built to impress, engineered to perform.</i>
</div>
