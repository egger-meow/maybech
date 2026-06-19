"""
Maybech service manager TUI for daemon services.
"""

import argparse
from threading import Thread

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Log

from src.daemon.runtime import create_default_runner
from src.daemon.service import DaemonRunner
from src.utils.logger import setup_logger


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
        self._buttons_created = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield DataTable(id="service-table")
            with Horizontal(id="services-button-row", classes="button-row"):
                pass
            yield Log(id="log-view")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Service", "Active", "Interval", "Last Tick", "Last Dur", "Errors")
        table.cursor_type = "row"
        self.update_table()
        self.set_interval(2.0, self.update_table)
        self.query_one(Log).write_line("Maybech Service Manager started.")

    def update_table(self) -> None:
        table = self.query_one(DataTable)
        current_row = table.cursor_row

        if not self._buttons_created:
            button_row = self.query_one("#services-button-row", Horizontal)
            for name in self.runner.services:
                btn = Button(f"Toggle {name.capitalize()}", id=f"btn-toggle-{name}")
                button_row.mount(btn)
            self._buttons_created = True

        table.clear()
        for name in self.runner.services:
            status = self.runner.get_service_status(name)
            if not status:
                continue

            active_label = "RUNNING" if status["active"] else "STOPPED"
            table.add_row(
                status["name"],
                active_label,
                f"{status['interval']}s",
                status["last_tick"] or "-",
                status["last_duration"] or "-",
                str(status["errors"]),
            )
            try:
                btn = self.query_one(f"#btn-toggle-{name}", Button)
                if status["active"]:
                    btn.label = f"Disable {name.capitalize()}"
                    btn.variant = "error"
                else:
                    btn.label = f"Enable {name.capitalize()}"
                    btn.variant = "success"
            except Exception:
                pass

        if current_row < table.row_count:
            table.move_cursor(row=current_row)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id and btn_id.startswith("btn-toggle-"):
            service_name = btn_id.replace("btn-toggle-", "")
            status = self.runner.get_service_status(service_name)
            if status:
                if status["active"]:
                    self.runner.disable_service(service_name)
                else:
                    self.runner.enable_service(service_name)
        self.update_table()

    def action_enable_all(self) -> None:
        for name in self.runner.services:
            self.runner.enable_service(name)
        self.update_table()

    def action_disable_all(self) -> None:
        for name in self.runner.services:
            self.runner.disable_service(name)
        self.update_table()


def main() -> None:
    parser = argparse.ArgumentParser(description="Maybech Service Manager")
    parser.add_argument("--headless", action="store_true", help="Run without TUI")
    parser.add_argument("--live", action="store_true", help="Disable dry-run for strategy")
    args = parser.parse_args()

    runner = create_default_runner(dry_run=not args.live)

    if args.headless:
        logger.info("Running in HEADLESS mode.")
        runner.run_forever()
        return

    daemon_thread = Thread(target=runner.run_forever, daemon=True)
    daemon_thread.start()
    ServiceManagerApp(runner).run()


if __name__ == "__main__":
    main()
