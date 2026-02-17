from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

class ExecutorView(Vertical):
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
