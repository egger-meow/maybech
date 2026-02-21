"""
Maybech Service Manager TUI — manage and monitor daemon services.
"""

import sys
import argparse
import logging
from threading import Thread

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, Log, Button
from textual.containers import Container, Horizontal, Vertical
from textual.binding import Binding

from src.daemon.service import DaemonRunner
from src.daemon.strategy_service import StrategyService
from src.daemon.notificator_service import NotificatorService
from src.utils.logger import setup_logger

# Configure logging
logger = setup_logger("service_manager")


class ServiceManagerApp(App):
    """Textual TUI for managing daemon services."""

    CSS = """
    Container {
        padding: 1;
    }
    #service-table {
        height: 10;
        border: solid green;
    }
    #log-view {
        height: 1fr;
        border: solid grey;
        margin-top: 1;
    }
    .button-row {
        height: auto;
        margin: 1;
    }
    Button {
        margin-right: 2;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh Status"),
        Binding("e", "enable_all", "Enable All"),
        Binding("d", "disable_all", "Disable All"),
    ]

    def __init__(self, runner: DaemonRunner):
        super().__init__()
        self.runner = runner

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield DataTable(id="service-table")
            with Horizontal(classes="button-row"):
                yield Button("Enable Strategy", id="btn-en-strategy", variant="success")
                yield Button("Disable Strategy", id="btn-dis-strategy", variant="error")
                yield Button("Enable Notificator", id="btn-en-notif", variant="success")
                yield Button("Disable Notificator", id="btn-dis-notif", variant="error")
            yield Log(id="log-view")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Service", "Active", "Interval", "Last Tick", "Last Dur", "Errors")
        table.cursor_type = "row"
        self.update_table()
        self.set_interval(2.0, self.update_table)
        
        # Connect logger to UI Log view
        log_view = self.query_one(Log)
        # In a real setup we'd use a custom handler, for now we just log a message
        log_view.write_line("Maybech Service Manager started.")

    def update_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for name in self.runner.services:
            status = self.runner.get_service_status(name)
            if status:
                table.add_row(
                    status["name"],
                    "✅" if status["active"] else "❌",
                    f"{status['interval']}s",
                    status["last_tick"] or "-",
                    status["last_duration"] or "-",
                    str(status["errors"])
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-en-strategy":
            self.runner.enable_service("strategy")
        elif btn_id == "btn-dis-strategy":
            self.runner.disable_service("strategy")
        elif btn_id == "btn-en-notif":
            self.runner.enable_service("notificator")
        elif btn_id == "btn-dis-notif":
            self.runner.disable_service("notificator")
        self.update_table()

    def action_enable_all(self) -> None:
        for name in self.runner.services:
            self.runner.enable_service(name)
        self.update_table()

    def action_disable_all(self) -> None:
        for name in self.runner.services:
            self.runner.disable_service(name)
        self.update_table()


def main():
    parser = argparse.ArgumentParser(description="Maybech Service Manager")
    parser.add_argument("--headless", action="store_true", help="Run without TUI")
    parser.add_argument("--live", action="store_true", help="Disable dry-run for strategy")
    args = parser.parse_args()

    # Create Runner
    runner = DaemonRunner()
    
    # Register Services
    # runner.register(StrategyService(dry_run=True))
    runner.register(NotificatorService())

    if args.headless:
        logger.info("Running in HEADLESS mode.")
        runner.run_forever()
    else:
        # Run daemon in a background thread
        daemon_thread = Thread(target=runner.run_forever, daemon=True)
        daemon_thread.start()

        # Run TUI
        app = ServiceManagerApp(runner)
        app.run()


if __name__ == "__main__":
    main()
