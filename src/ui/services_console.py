from collections import deque
import logging

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Static, DataTable, Log, Button

from src.daemon.service import DaemonRunner

logger = logging.getLogger(__name__)

class GUILogHandler(logging.Handler):
    """Custom logging handler to send logs to the Textual UI Log widget."""
    def __init__(self, app, log_widget_id: str):
        super().__init__()
        self.app = app
        self.log_widget_id = log_widget_id
        # We only keep the last N messages to avoid excessive memory usage
        self.history = deque(maxlen=1000)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.history.append(msg)
            
            # Use call_from_thread to ensure thread safety when updating the UI
            try:
                log_widget = self.app.query_one(f"#{self.log_widget_id}", Log)
                self.app.call_from_thread(log_widget.write_line, msg)
            except Exception:
                # View might not be mounted yet or currently inaccessible
                pass
        except Exception:
            self.handleError(record)


class ServicesConsoleView(Vertical):
    """Generic Services Console Page."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="services-header", classes="top-section"):
            yield Label("Services Console", classes="section-title")
        
        with Vertical(id="services-status-container", classes="box"):
            yield Label("Daemon Services Status:", classes="label")
            yield DataTable(id="services-table")
            
            with Horizontal(id="services-button-row", classes="button-row"):
                # Buttons will be dynamically added here based on registered services
                pass

            yield Label("Daemon Logs", classes="section-title")
            yield Log(id="services-logs", classes="box")

    def on_mount(self) -> None:
        table = self.query_one("#services-table", DataTable)
        table.add_columns("Service", "Active", "Interval", "Last Tick", "Last Dur", "Errors")
        table.cursor_type = "row"
        
        # The button row will be populated properly in the first update_status_ui
        self._buttons_created = False

    def update_status_ui(self, runner: DaemonRunner) -> None:
        """Update UI with status data from the given runner (must run on main thread)."""
        if not runner:
            return

        table = self.query_one("#services-table", DataTable)
        
        # Save current cursor position before clearing
        current_row = table.cursor_row
        
        table.clear()
        
        # Populate dynamic buttons if not done yet
        if not self._buttons_created:
            button_row = self.query_one("#services-button-row", Horizontal)
            for name in runner.services:
                btn_id = f"btn-toggle-{name}"
                # Initially create a button, we'll update its label/variant shortly
                btn = Button(f"Toggle {name.capitalize()}", id=btn_id)
                button_row.mount(btn)
            self._buttons_created = True

        for name in runner.services:
            status = runner.get_service_status(name)
            if status:
                table.add_row(
                    status["name"].capitalize(),
                    "✅ RUNNING" if status["active"] else "❌ STOPPED",
                    f"{status['interval']}s",
                    status["last_tick"] or "-",
                    status["last_duration"] or "-",
                    str(status["errors"])
                )
                
                # Update button text and class based on active status
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
        
        # Restore cursor position if possible
        if current_row < table.row_count:
            table.move_cursor(row=current_row)

    def attach_logger(self, app):
        """Attaches the custom logging handler to the root logger or specific loggers."""
        handler = GUILogHandler(app, "services-logs")
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        
        # Attach to root logger to catch everything, or specific ones
        logging.getLogger().addHandler(handler)
        
        # If there's already history from before the view started (or after a reload), write it
        log_widget = self.query_one("#services-logs", Log)
        for msg in handler.history:
            log_widget.write_line(msg)
