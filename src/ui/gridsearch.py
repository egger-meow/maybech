from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Select, ProgressBar, Static

class GridSearchView(Vertical):
    """Grid Search Optimizer View."""

    def compose(self) -> ComposeResult:
        yield Label("Grid Search Parameters", classes="section-title")
        with Horizontal(classes="controls-area"):
            yield Label("Strategy:")
            yield Select([("Momentum", "momentum")], id="gs-strategy", allow_blank=False, value="momentum")
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
        with Vertical(id="gs-results-container", classes="box"):
            yield Static("Ready to optimize.", id="gs-status-msg")
            yield ProgressBar(total=100, show_percentage=True, id="gs-progress", classes="hidden")
            yield DataTable(id="gs-results-table")

    def on_mount(self) -> None:
        table = self.query_one("#gs-results-table", DataTable)
        table.add_columns("Rank", "K-Long", "K-Short", "Gap", "Total PnL", "Win Rate", "Trades")
        table.cursor_type = "row"
        table.zebra_stripes = True
