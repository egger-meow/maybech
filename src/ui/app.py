import json
import logging
import sys
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, Select, ContentSwitcher
from textual import work

from src.backtesting.engine import BacktestEngine
from src.backtesting.optimizer import GridSearch
from src.config.settings import settings
from src.config.strategy import StrategyConfig, MomentumConfig
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.monitor.dashboard import Dashboard
from src.strategies.momentum import MomentumStrategy
from src.utils.logger import setup_logger

from src.ui.utils import format_time_taipei
from src.ui.dashboard import DashboardView
from src.ui.backtest import BacktestView
from src.ui.services_console import ServicesConsoleView, GUILogHandler
from src.daemon.service import DaemonRunner
from src.daemon.strategy_service import StrategyService
from src.daemon.notificator_service import NotificatorService
from src.ui.settings import SettingsView
from src.ui.gridsearch import GridSearchView

logger = logging.getLogger(__name__)

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
    .controls-area {
        height: auto;
        margin: 1 0;
        align-vertical: middle;
    }
    .controls-area Label { padding: 1; width: auto; }
    .controls-area Input { width: 25; }
    .controls-area Select { width: 25; }
    
    .input-group {
        height: auto;
        margin-bottom: 1;
    }
    .input-pair {
        width: 1fr;
        height: auto;
        align-vertical: middle;
    }
    .input-pair Label { width: 15; padding: 1; }
    .input-pair Input { width: 20; }
    
    .button-row {
        height: auto;
        margin: 1 0;
        align-vertical: middle;
    }

    /* Services Console Specific */
    #services-header { height: auto; align-vertical: middle; background: $accent; }
    #services-header .section-title { width: auto; background: transparent; }
    .label { text-style: italic; color: $text-muted; margin-top: 1; }
    #services-logs { height: 1fr; }
    
    .warning { color: $error; text-style: bold; margin-left: 2; }
    .status-msg { color: $success; margin-left: 2; }
    .hidden { display: none; }
    #btn-save-config { margin-left: 0; }
    DataTable { height: 1fr; border: solid $secondary; }
    #gs-results-container DataTable { border: none; }
    #gs-results-container { height: 1fr; }
    #view-switcher { height: 1fr; }

    #preload-container {
        border: solid $accent;
        background: $accent-darken-1;
    }
    #preload-title { text-style: bold; }
    #preload-status { color: $text-muted; text-style: italic; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "show_dashboard", "Dashboard"),
        ("2", "show_backtest", "Backtest"),
        ("3", "show_settings", "Settings"),
        ("4", "show_services_console", "Services Console"),
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
            self.strategy = MomentumStrategy(config=self.config.momentum)
            self.engine = BacktestEngine(self.strategy, self.candle_manager)
            self.optimizer = GridSearch(self.candle_manager)
            
            # Views
            self.dashboard_view = DashboardView(id="view-dashboard")
            self.backtest_view = BacktestView(id="view-backtest")
            self.settings_view = SettingsView(id="view-settings")
            self.services_console_view = ServicesConsoleView(id="view-services-console")
            self.gridsearch_view = GridSearchView(id="view-gridsearch")
            
            # Daemons
            self.runner = DaemonRunner()
            self.runner.register(StrategyService(dry_run=True))
            self.runner.register(NotificatorService())
            
        except Exception as e:
            logger.exception("Init failed")
            # In a real app we might want to re-raise or handle this more gracefully
            # but keep original behavior for now
            sys.exit(f"Init failed: {e}")

    def compose(self) -> ComposeResult:
        yield Header()
        with ContentSwitcher(initial="view-dashboard", id="view-switcher"):
            yield self.dashboard_view
            yield self.backtest_view
            yield self.settings_view
            yield self.services_console_view
            yield self.gridsearch_view
        yield Footer()

    def on_mount(self) -> None:
        # Schedule auto-refresh every 10s
        self.set_interval(10.0, self.action_refresh_dashboard)
        self.update_settings_readonly()

    def on_ready(self) -> None:
        # CALLING the @work method directly starts the worker.
        # on_ready ensures all widgets are mounted and ready for querying.
        
        # Attach custom logger handler
        handler = GUILogHandler(self, "services-logs")
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        
        import threading
        self.daemon_thread = threading.Thread(target=self.runner.run_forever, daemon=True)
        self.daemon_thread.start()

        self.preload_data_task()
        self.action_refresh_dashboard()

    @work(exclusive=True, thread=True)
    def preload_data_task(self) -> None:
        """Preload market data for all pairs on startup."""
        logger.info("PRELOAD_DEBUG: Task started")
        # Ensure UI has settled
        time.sleep(1.0)
        
        try:
            # Try app-level query first
            container = self.query_one("#preload-container")
            progress = self.query_one("#preload-progress", ProgressBar)
            status = self.query_one("#preload-status", Static)
            logger.info("PRELOAD_DEBUG: UI widgets found")
        except Exception as e:
            logger.error(f"PRELOAD_DEBUG: UI widgets not found: {e}")
            # Fallback to direct ref if app-level query fails
            try:
                container = self.dashboard_view.query_one("#preload-container")
                progress = self.dashboard_view.query_one("#preload-progress")
                status = self.dashboard_view.query_one("#preload-status")
                logger.info("PRELOAD_DEBUG: UI widgets found via dashboard_view")
            except Exception as e2:
                logger.error(f"PRELOAD_DEBUG: Absolute fallback failed: {e2}")
                return
        
        self.call_from_thread(container.remove_class, "hidden")
        
        pairs = settings.TRADING_PAIRS
        total = len(pairs)
        duration = 10 
        
        logger.info(f"PRELOAD_DEBUG: Preloading {total} pairs: {pairs}")
        self.call_from_thread(progress.update, total=total, progress=0)
        
        for i, pair in enumerate(pairs):
            self.call_from_thread(status.update, f"Preloading {pair} ({duration} days)...")
            try:
                self.candle_manager.get_history(pair, settings.CANDLE_INTERVAL, days=duration)
            except Exception as e:
                logger.error(f"PRELOAD_DEBUG: Fetch failed for {pair}: {e}")
            
            self.call_from_thread(progress.update, progress=i+1)
            time.sleep(0.1)
            
        self.call_from_thread(status.update, "Preload Complete!")
        time.sleep(1.0)
        self.call_from_thread(container.add_class, "hidden")
        logger.info("PRELOAD_DEBUG: Task finished successfully")

    # -- Navigation --
    def action_show_dashboard(self) -> None:
        self._switch_view("view-dashboard", "Dashboard")
        self.action_refresh_dashboard()

    def action_show_backtest(self) -> None:
        self._switch_view("view-backtest", "Backtest")

    def action_show_settings(self) -> None:
        self._switch_view("view-settings", "Settings")
        self.update_settings_readonly()

    def action_show_services_console(self) -> None:
        self._switch_view("view-services-console", "Services Console")
        self.action_refresh_dashboard()

    def action_show_gridsearch(self) -> None:
        self._switch_view("view-gridsearch", "Grid Search")

    def _switch_view(self, view_id: str, title_suffix: str) -> None:
        self.query_one("#view-switcher", ContentSwitcher).current = view_id
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
            summary = self.dashboard.get_account_summary()
            positions = self.dashboard.get_open_positions()
            
            prices = {}
            fng = {}
            ema = 0.0
            mvrv = 0.0

            self.call_from_thread(self._update_ui, summary, positions, prices, fng, ema, mvrv)
                
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
        finally:
            self._refreshing = False

    def _update_ui(self, summary, positions, prices, fng, ema, mvrv):
        """Update all UI elements (dashboard + services console)."""

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

        try:
            if self.services_console_view.is_mounted:
                self.services_console_view.update_status_ui(self.runner)
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
            
        elif btn_id and btn_id.startswith("btn-toggle-"):
            service_name = btn_id.replace("btn-toggle-", "")
            status = self.runner.get_service_status(service_name)
            if status:
                if status["active"]:
                    self.runner.disable_service(service_name)
                else:
                    self.runner.enable_service(service_name)
            if self.services_console_view.is_mounted:
                self.services_console_view.update_status_ui(self.runner)

    @work(exclusive=True, thread=True)
    def save_config_task(self) -> None:
        """Save settings from UI to JSON."""
        try:
            k_long = float(self.settings_view.query_one("#in-k-long", Input).value)
            k_short = float(self.settings_view.query_one("#in-k-short", Input).value)
            gap = float(self.settings_view.query_one("#in-gap", Input).value)
            tp_ratio = float(self.settings_view.query_one("#in-tp-ratio", Input).value)
            tp_vol_str = self.settings_view.query_one("#in-tp-vol", Input).value
            tp_vol = True if tp_vol_str.strip() == "1" else False
            
            active_strat = self.settings_view.query_one("#sel-strategy", Select).value
            if not active_strat:
                active_strat = "momentum"

            mom_cfg = MomentumConfig(
                k_long=k_long, 
                k_short=k_short, 
                gap_threshold=gap,
                stop_win_ratio=tp_ratio,
                stop_win_vol_ratio=tp_vol
            )
            new_cfg = StrategyConfig(momentum=mom_cfg, active_strategy=active_strat)
            new_cfg.save()
            
            self.config = new_cfg
            
            if active_strat == "momentum":
                self.strategy = MomentumStrategy(config=mom_cfg)
            
            self.engine.strategy = self.strategy
            
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
            status_msg = self.gridsearch_view.query_one("#gs-status-msg", Static)
            progress = self.gridsearch_view.query_one("#gs-progress", ProgressBar)
            
            kl_str = self.gridsearch_view.query_one("#gs-klong", Input).value
            ks_str = self.gridsearch_view.query_one("#gs-kshort", Input).value
            gap_str = self.gridsearch_view.query_one("#gs-gap", Input).value
            days_str = self.gridsearch_view.query_one("#gs-days", Input).value
        except Exception:
            return

        self.call_from_thread(table.clear)
        self.call_from_thread(status_msg.update, "Starting Grid Search...")
        self.call_from_thread(progress.remove_class, "hidden")
        
        try:
            days = int(days_str)
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
            
            def on_progress(curr, total):
                self.call_from_thread(progress.update, total=total, progress=curr)
                self.call_from_thread(status_msg.update, f"Optimizing: {curr}/{total} combinations...")

            results = self.optimizer.optimize(
                "ETH-USDT-SWAP", kl_range, ks_range, gap_range, days=days, bar=settings.CANDLE_INTERVAL,
                on_progress=on_progress
            )

            rows = []
            for i, res in enumerate(results[:20]): 
                rows.append((
                    str(i+1),
                    f"{res.config.momentum.k_long:.1f}",
                    f"{res.config.momentum.k_short:.1f}",
                    f"{res.config.momentum.gap_threshold:.1f}",
                    f"{res.result.total_pnl:.4f}",
                    f"{res.result.win_rate*100:.1f}%",
                    str(res.result.total_trades)
                ))
            
            self.app.call_from_thread(status_msg.update, f"Optimization Complete! Best PnL: {results[0].result.total_pnl:.4f}")
            self.app.call_from_thread(progress.add_class, "hidden")
            self.app.call_from_thread(self._populate_table, table, rows)

        except Exception as e:
            logger.exception("Grid Search failed")
            self.call_from_thread(status_msg.update, f"Optimization Failed: {e}")
            self.call_from_thread(progress.add_class, "hidden")

    def _populate_table(self, table: DataTable, rows: list) -> None:
        try:
            for r in rows:
                table.add_row(*r)
        except Exception:
            pass
