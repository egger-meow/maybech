from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, Static, ProgressBar

from src.ui.utils import format_time_taipei

class DashboardView(Vertical):
    """Account summary, Market Overview, and open positions."""

    def compose(self) -> ComposeResult:
        with Vertical(id="preload-container", classes="box hidden"):
            yield Label("Preloading Market Data...", id="preload-title")
            yield ProgressBar(total=100, show_percentage=True, id="preload-progress")
            yield Static("", id="preload-status")

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
