"""
Maybech — Crypto Auto-Trader
Interactive Terminal UI (TUI) using Textual.
"""

import json
import logging
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Static
)
from textual import work

from src.backtesting.engine import BacktestEngine
from src.backtesting.optimizer import GridSearch
from src.config.settings import settings
from src.config.strategy import StrategyConfig
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.monitor.dashboard import Dashboard
from src.strategies.momentum import MomentumStrategy
from src.utils.logger import setup_logger

# Configure logging
logger = setup_logger("") # Configure root logger so all modules log
logging.getLogger("src.exchange.client").setLevel(logging.WARNING)
logging.getLogger("src.data.candles").setLevel(logging.WARNING)
logging.getLogger("src.backtesting.optimizer").setLevel(logging.WARNING)
# logging.getLogger("src.data.market").setLevel(logging.WARNING) # Enable for debugging
logging.getLogger("httpx").setLevel(logging.WARNING)

# Timezone Helper
TZ_TAIPEI = timezone(timedelta(hours=8))

def format_time_taipei(ts_str: str | None) -> str:
    if not ts_str:
        return ""
    try:
        if hasattr(ts_str, "astimezone"):
            dt = ts_str
        else:
            dt = datetime.fromtimestamp(int(ts_str) / 1000, tz=timezone.utc)
        return dt.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts_str)


class DashboardView(Container):
    """Account summary, Market Overview, and open positions."""

    def compose(self) -> ComposeResult:
        with Vertical(classes="top-section"):
            yield Label("Account Summary", classes="section-title")
            yield Static(id="balance-summary", classes="box")

        yield Label("Open Positions", classes="section-title")
        yield DataTable(id="positions-table")

    def on_mount(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("Inst ID", "Side", "Pos", "Avg Px", "Mark Px", "UPL", "Time (TPE)")
        table.cursor_type = "row"
        table.zebra_stripes = True


class BacktestView(Container):
    """Backtest configuration and results."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls-area"):
            yield Label("Symbol:")
            yield Input(placeholder="ETH-USDT-SWAP", id="bt-symbol", value="ETH-USDT-SWAP")
            yield Label("Days:")
            yield Input(placeholder="7", id="bt-days", value="7")
            yield Button("Run Backtest", id="bt-run-btn", variant="primary")
        
        with Vertical(id="bt-results-area"):
            yield Label("Results", classes="section-title")
            yield Static(id="bt-stats", classes="box")
            yield DataTable(id="bt-trades-table")

    def on_mount(self) -> None:
        table = self.query_one("#bt-trades-table", DataTable)
        table.add_columns("Time (TPE)", "Signal", "Entry", "Exit", "PnL", "Result", "Reason")
        table.cursor_type = "row"
        table.zebra_stripes = True


class ExecutorView(Container):
    """Mock Executor Status Page."""

    def compose(self) -> ComposeResult:
        yield Label("Strategy Executor (Mock)", classes="section-title")
        yield Static(id="executor-status", classes="box")
        yield Label("Daemon Logs", classes="section-title")
        yield Static(id="executor-logs", classes="box")

    def on_mount(self) -> None:
        pass # Status updated via action_refresh_dashboard loop

    def update_status_ui(self, data: dict) -> None:
        """Update UI with status data (must run on main thread)."""
        if not data:
            try:
                self.query_one("#executor-status", Static).update("Daemon status unavailable.")
            except: pass
            return

        try:
            status_txt = (
                f"Status: {data.get('status', 'UNKNOWN')}\n"
                f"Last Update: {data.get('last_update', 'N/A')}\n"
                f"Strategy: {data.get('strategy', 'N/A')}\n"
                f"Mode: {'DRY RUN' if data.get('dry_run') else 'LIVE'}\n"
            )
            self.query_one("#executor-status", Static).update(status_txt)
            
            signals = data.get("signals", [])
            log_txt = "\n".join([f"[{s['time']}] {s['pair']}: {s['signal']} -> {s['result']}" for s in signals[-5:]]) if signals else "No recent signals."
            self.query_one("#executor-logs", Static).update(log_txt)
        except Exception:
            pass


class SettingsView(Container):
    """Editable settings view."""

    def compose(self) -> ComposeResult:
        yield Label("Strategy Configuration (Editable)", classes="section-title")
        
        with Horizontal(classes="controls-area"):
            yield Label("K Long:")
            yield Input(id="in-k-long")
            yield Label("K Short:")
            yield Input(id="in-k-short")
        
        with Horizontal(classes="controls-area"):
            yield Label("Gap Thresh:")
            yield Input(id="in-gap")
            yield Button("Save Config", id="btn-save-config", variant="primary")
        
        # Status Label to avoid popup overlap
        yield Label("", id="settings-status", classes="status-msg")

        yield Label("Static Configuration (Read-Only)", classes="section-title")
        yield Static(id="settings-readonly", classes="box")

    def on_mount(self) -> None:
        cfg = StrategyConfig.load()
        self.query_one("#in-k-long", Input).value = str(cfg.k_long)
        self.query_one("#in-k-short", Input).value = str(cfg.k_short)
        self.query_one("#in-gap", Input).value = str(cfg.gap_threshold)


class GridSearchView(Container):
    """Grid Search Optimizer View."""

    def compose(self) -> ComposeResult:
        yield Label("Grid Search Parameters", classes="section-title")
        with Horizontal(classes="controls-area"):
            yield Label("K-Long (start,end,step):")
            yield Input(placeholder="5,15,5", id="gs-klong", value="5,15,5")
        with Horizontal(classes="controls-area"):
            yield Label("K-Short (start,end,step):")
            yield Input(placeholder="2,10,4", id="gs-kshort", value="2,10,4")
        with Horizontal(classes="controls-area"):
            yield Label("Gap (start,end,step):")
            yield Input(placeholder="1.0,5.0,2.0", id="gs-gap", value="1.0,5.0,2.0")
        
        with Horizontal(classes="controls-area"):
            yield Label("Days to Test:")
            yield Input(placeholder="7", id="gs-days", value="7")

        with Horizontal(classes="controls-area"):
            yield Button("Run Optimization", id="gs-run-btn", variant="primary")
            yield Label("WARNING: CPU Intensive!", classes="warning")

        yield Label("Top Results", classes="section-title")
        yield DataTable(id="gs-results-table")

    def on_mount(self) -> None:
        table = self.query_one("#gs-results-table", DataTable)
        table.add_columns("Rank", "K-Long", "K-Short", "Gap", "Total PnL", "Win Rate", "Trades")
        table.cursor_type = "row"
        table.zebra_stripes = True


class MaybechApp(App):
    """Main TUI Application."""
    
    CSS = """
    Screen { layout: vertical; }
    .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
        padding-left: 1;
        background: $accent;
        color: $text;
        width: 100%;
    }
    .box {
        border: solid $secondary;
        padding: 1;
        margin-bottom: 1;
        height: auto;
    }
    .top-section { height: auto; }
    .half-width { width: 50%; }
    .controls-area {
        height: auto;
        margin: 1 0;
        align-vertical: middle;
    }
    .controls-area Label { padding: 1; width: auto; }
    .controls-area Input { width: 16; }
    .warning { color: $error; text-style: bold; margin-left: 2; }
    .status-msg { color: $success; margin-left: 2; }
    #btn-save-config { margin-left: 2; }
    DataTable { height: 1fr; border: solid $secondary; }
    #view-container { height: 1fr; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "show_dashboard", "Dashboard"),
        ("2", "show_backtest", "Backtest"),
        ("3", "show_settings", "Settings"),
        ("4", "show_executor", "Executor"),
        ("5", "show_gridsearch", "Grid Search"),
        ("r", "refresh_dashboard", "Refresh Data"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.title = "Maybech Auto-Trader"
        self._refreshing = False  # Concurrency guard
        try:
            self.client = OKXClient()
            self.candle_manager = CandleManager(self.client)
            self.dashboard = Dashboard(self.client)
            
            # Load initial config
            self.config = StrategyConfig.load()
            self.strategy = MomentumStrategy(config=self.config)
            self.engine = BacktestEngine(self.strategy, self.candle_manager)
            self.optimizer = GridSearch(self.candle_manager)
            
            # Views
            self.dashboard_view = DashboardView(id="view-dashboard")
            self.backtest_view = BacktestView(id="view-backtest")
            self.settings_view = SettingsView(id="view-settings")
            self.executor_view = ExecutorView(id="view-executor")
            self.gridsearch_view = GridSearchView(id="view-gridsearch")
            
            # Default view
            self.current_view = self.dashboard_view
            
        except Exception as e:
            logger.exception("Init failed")
            sys.exit(f"Init failed: {e}")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(self.dashboard_view, id="view-container")
        yield Footer()

    def on_mount(self) -> None:
        # Schedule auto-refresh every 10s
        self.set_interval(10.0, self.action_refresh_dashboard)
        
        # Initial data load
        self.action_refresh_dashboard()
        self.update_settings_readonly()

    # -- Navigation --
    def action_show_dashboard(self) -> None:
        self._switch_view(self.dashboard_view, "Dashboard")
        # Refresh happens automatically via interval, but we can trigger one if needed.
        # But let's rely on interval to avoid spamming if user switches fast.
        self.action_refresh_dashboard()

    def action_show_backtest(self) -> None:
        self._switch_view(self.backtest_view, "Backtest")

    def action_show_settings(self) -> None:
        self._switch_view(self.settings_view, "Settings")
        self.update_settings_readonly()

    def action_show_executor(self) -> None:
        self._switch_view(self.executor_view, "Executor (Mock)")
        # Trigger an update immediate
        self.action_refresh_dashboard()

    def action_show_gridsearch(self) -> None:
        self._switch_view(self.gridsearch_view, "Grid Search")

    def _switch_view(self, new_view: Container, title_suffix: str) -> None:
        container = self.query_one("#view-container", Container)
        
        # If view isn't mounted, mount it. If it is, ensure it's displayed.
        # But for switching logic in a Container, we typically remove current and mount new.
        if self.current_view and self.current_view != new_view:
            self.current_view.remove()
            container.mount(new_view)
            self.current_view = new_view
        
        self.title = f"Maybech - {title_suffix}"

    # -- Logic --
    def update_settings_readonly(self) -> None:
        content = (
            f"Mode: {'DEMO' if settings.OKX_FLAG == '1' else 'LIVE'}\n"
            f"Candle Interval: {settings.CANDLE_INTERVAL}\n"
            f"Trade Size: {settings.TRADE_QUANTITY_ETH} ETH\n"
            f"Pairs: {settings.TRADING_PAIRS}"
        )
        try:
            self.settings_view.query_one("#settings-readonly", Static).update(content)
        except Exception:
            pass 

    @work(exclusive=True, thread=True)
    def action_refresh_dashboard(self) -> None:
        """Fetch live data in background thread (prevent UI freeze)."""
        if self._refreshing:
            return
        self._refreshing = True
        
        try:
            # 1. Dashboard Data
            summary = self.dashboard.get_account_summary()
            positions = self.dashboard.get_open_positions()
            
            # 2. Market Data (Removed)
            prices = {}
            fng = {}
            ema = 0.0
            mvrv = 0.0

            # 3. Executor Status (Read File)
            daemon_data = {}
            try:
                path = Path("data/daemon_status.json")
                if path.exists():
                    with open(path, "r") as f:
                        daemon_data = json.load(f)
            except Exception: pass

            # Update UI (Main Thread)
            self.call_from_thread(self._update_ui, summary, positions, prices, fng, ema, mvrv, daemon_data)
                
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
        finally:
            self._refreshing = False

    def _update_ui(self, summary, positions, prices, fng, ema, mvrv, daemon_data):
        """Update all UI elements (dashboard + executor)."""
        # Dashboard
        try:
            balance_static = self.dashboard_view.query_one("#balance-summary", Static)
            if summary:
                txt = (
                    f"Total Equity: {summary.get('total_equity', '0')} USDT\n"
                    f"Avail Equity: {summary.get('available_equity', '0')} USDT\n"
                    f"Unreal PnL  : {summary.get('currencies', [{}])[0].get('unrealised_pnl', '0')} USDT"
                )
                balance_static.update(txt)
            else:
                balance_static.update("No account data.")


            
            table = self.dashboard_view.query_one("#positions-table", DataTable)
            table.clear()
            for p in positions:
                time_str = format_time_taipei(p.get('update_time')) 
                table.add_row(
                    p['inst_id'], p['pos_side'], p['position'], 
                    p['avg_price'], p.get('mark_price', ''), 
                    p['unrealised_pnl'], time_str
                )
        except Exception:
            pass

        # Executor View (Update if mounted)
        try:
            if self.executor_view.is_mounted:
                self.executor_view.update_status_ui(daemon_data)
        except Exception:
            pass 

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        
        if btn_id == "bt-run-btn":
            try:
                symbol = self.backtest_view.query_one("#bt-symbol", Input).value
                days = self.backtest_view.query_one("#bt-days", Input).value
                self.run_backtest_task(symbol, days)
            except Exception:
                pass
                
        elif btn_id == "btn-save-config":
            self.save_config_task()

        elif btn_id == "gs-run-btn":
            self.run_gridsearch_task()

    @work(exclusive=True, thread=True)
    def save_config_task(self) -> None:
        """Save settings from UI to JSON."""
        try:
            k_long = float(self.settings_view.query_one("#in-k-long", Input).value)
            k_short = float(self.settings_view.query_one("#in-k-short", Input).value)
            gap = float(self.settings_view.query_one("#in-gap", Input).value)
            
            new_cfg = StrategyConfig(k_long=k_long, k_short=k_short, gap_threshold=gap)
            new_cfg.save()
            
            self.config = new_cfg
            self.strategy.config = new_cfg
            
            self.app.call_from_thread(
                self.settings_view.query_one("#settings-status", Label).update, "Settings Saved!"
            )
            
        except ValueError:
            self.app.call_from_thread(
                self.settings_view.query_one("#settings-status", Label).update, "Error: Invalid Input"
            )

    @work(exclusive=True, thread=True)
    def run_backtest_task(self, symbol: str, days_str: str) -> None:
        try:
            stat_box = self.backtest_view.query_one("#bt-stats", Static)
            table = self.backtest_view.query_one("#bt-trades-table", DataTable)
        except Exception:
            return 

        self.call_from_thread(stat_box.update, "Running backtest... please wait.")
        self.call_from_thread(table.clear)

        try:
            days = int(days_str)
            interval = settings.CANDLE_INTERVAL 
            self.engine.strategy = self.strategy
            result = self.engine.run(symbol, bar=interval, days=days)
            
            stats_txt = (
                f"Trades: {result.total_trades} | "
                f"Win Rate: {result.win_rate*100:.1f}% | "
                f"Profit Factor: {result.profit_factor:.2f}\n"
                f"Total PnL: {result.total_pnl:.4f}\n"
            )
            self.call_from_thread(stat_box.update, stats_txt)

            rows = []
            sorted_trades = sorted(result.trades, key=lambda x: x.entry_time, reverse=True)
            for t in sorted_trades:
                res_str = "WIN" if t.is_win else "LOSS"
                time_str = format_time_taipei(t.entry_time)
                rows.append((
                    time_str, t.signal, 
                    f"{t.entry_price:.2f}", f"{t.exit_price:.2f}", 
                    f"{t.pnl:.4f}", res_str, t.exit_reason
                ))
            
            self.app.call_from_thread(self._populate_table, table, rows)
            
        except Exception as e:
            self.call_from_thread(stat_box.update, f"Error: {e}")

    @work(exclusive=True, thread=True)
    def run_gridsearch_task(self) -> None:
        """Run grid search optimization."""
        try:
            table = self.gridsearch_view.query_one("#gs-results-table", DataTable)
            kl_str = self.gridsearch_view.query_one("#gs-klong", Input).value
            ks_str = self.gridsearch_view.query_one("#gs-kshort", Input).value
            gap_str = self.gridsearch_view.query_one("#gs-gap", Input).value
            days_str = self.gridsearch_view.query_one("#gs-days", Input).value
        except Exception:
            return

        self.call_from_thread(table.clear)
        
        try:
            days = int(days_str)
            # Parse ranges
            def parse_range(s):
                parts = [float(x.strip()) for x in s.split(",")]
                start, end, step = parts[0], parts[1], parts[2]
                vals = []
                curr = start
                while curr <= end + 0.0001: 
                    vals.append(curr)
                    curr += step
                return vals

            kl_range = parse_range(kl_str)
            ks_range = parse_range(ks_str)
            gap_range = parse_range(gap_str)
            
            results = self.optimizer.optimize(
                "ETH-USDT-SWAP", kl_range, ks_range, gap_range, days=days, bar=settings.CANDLE_INTERVAL
            )

            rows = []
            for i, res in enumerate(results[:20]): 
                rows.append((
                    str(i+1),
                    f"{res.config.k_long:.1f}",
                    f"{res.config.k_short:.1f}",
                    f"{res.config.gap_threshold:.1f}",
                    f"{res.result.total_pnl:.4f}",
                    f"{res.result.win_rate*100:.1f}%",
                    str(res.result.total_trades)
                ))
            
            self.app.call_from_thread(self._populate_table, table, rows)

        except Exception as e:
            logger.exception("Grid Search failed")

    def _populate_table(self, table: DataTable, rows: list) -> None:
        try:
            for r in rows:
                table.add_row(*r)
        except Exception:
            pass


if __name__ == "__main__":
    app = MaybechApp()
    app.run()
