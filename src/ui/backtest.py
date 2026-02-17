from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, DataTable, Input, Label, Select, Static

class BacktestView(Vertical):
    """Backtest configuration and results."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls-area"):
            yield Label("Strategy:")
            yield Select([("Momentum", "momentum")], id="bt-strategy", allow_blank=False, value="momentum")
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
