from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Static, ProgressBar

class ExecutorView(Vertical):
    """Mock Executor Status Page."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="executor-header"):
            yield Label("Strategy Executor", classes="section-title")
            yield Label("OFFLINE", id="executor-live-indicator")
        
        with Vertical(id="executor-status-container", classes="box"):
            yield Label("Daemon Status:", classes="label")
            yield Static("Searching for daemon...", id="executor-status")
            yield Label("Polling Progress:", classes="label")
            yield ProgressBar(total=10, show_percentage=False, id="executor-progress")

        yield Label("Recent Signals", classes="section-title")
        yield Static(id="executor-logs", classes="box")

    def on_mount(self) -> None:
        pass # Status updated via action_refresh_dashboard loop

    def update_status_ui(self, data: dict, seconds_since_update: float = 0) -> None:
        """Update UI with status data (must run on main thread)."""
        status_static = self.query_one("#executor-status", Static)
        indicator = self.query_one("#executor-live-indicator", Label)
        progress = self.query_one("#executor-progress", ProgressBar)

        if not data:
            status_static.update("Daemon status unavailable. Please start `run_daemon.py`.")
            indicator.update("OFFLINE")
            indicator.set_class(False, "online")
            progress.progress = 0
            return

        try:
            # Determine if daemon is "online" (updated in the last 30s)
            is_online = seconds_since_update < 30
            
            if is_online:
                indicator.update("● RUNNING")
                indicator.set_class(True, "online")
                # Update progress bar (0-10 scale based on 10s poll)
                # If seconds_since_update > 10, it just stays at max until reset
                progress.progress = min(10, seconds_since_update)
            else:
                indicator.update("○ STALE")
                indicator.set_class(False, "online")
                progress.progress = 0

            status_txt = (
                f"Status: {data.get('status', 'UNKNOWN')}\n"
                f"Last Update: {data.get('last_update', 'N/A')} ({int(seconds_since_update)}s ago)\n"
                f"Strategy: {data.get('strategy', 'N/A')}\n"
                f"Mode: {'DRY RUN' if data.get('dry_run') else 'LIVE'}\n"
            )
            status_static.update(status_txt)
            
            signals = data.get("signals", [])
            log_txt = "\n".join([f"[{s['time']}] {s['pair']}: {s['signal']} -> {s['result']}" for s in signals[-10:]]) if signals else "No recent signals."
            self.query_one("#executor-logs", Static).update(log_txt)
        except Exception:
            pass
