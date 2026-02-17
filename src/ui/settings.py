from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from src.config.strategy import StrategyConfig

class SettingsView(Vertical):
    """Editable settings view."""

    def compose(self) -> ComposeResult:
        yield Label("Strategy (Global)", classes="section-title")
        with Horizontal(classes="controls-area"):
            yield Label("Active Strategy:")
            yield Select([("Momentum", "momentum")], id="sel-strategy", allow_blank=False, value="momentum")

        yield Label("Momentum Parameters", classes="section-title")
        with Vertical(id="cfg-momentum", classes="box"):
            with Horizontal(classes="input-group"):
                with Horizontal(classes="input-pair"):
                    yield Label("K Long:")
                    yield Input(id="in-k-long")
                with Horizontal(classes="input-pair"):
                    yield Label("K Short:")
                    yield Input(id="in-k-short")
            
            with Horizontal(classes="input-group"):
                with Horizontal(classes="input-pair"):
                    yield Label("Gap Thresh:")
                    yield Input(id="in-gap")
                with Horizontal(classes="input-pair"):
                    yield Label("TP Ratio:")
                    yield Input(id="in-tp-ratio")

            with Horizontal(classes="input-group"):
                with Horizontal(classes="input-pair"):
                    yield Label("Vol Scaled TP:")
                    yield Input(id="in-tp-vol", placeholder="1=True, 0=False")

        with Horizontal(classes="button-row"):
            yield Button("Save Config", id="btn-save-config", variant="primary")
            yield Label("", id="settings-status", classes="status-msg")

        yield Label("Static Configuration (Read-Only)", classes="section-title")
        yield Static(id="settings-readonly", classes="box")

    def on_mount(self) -> None:
        cfg = StrategyConfig.load()
        
        # Set Active Strategy
        self.query_one("#sel-strategy", Select).value = cfg.active_strategy

        # Load Momentum Params
        self.query_one("#in-k-long", Input).value = str(cfg.momentum.k_long)
        self.query_one("#in-k-short", Input).value = str(cfg.momentum.k_short)
        self.query_one("#in-gap", Input).value = str(cfg.momentum.gap_threshold)
        self.query_one("#in-tp-ratio", Input).value = str(cfg.momentum.stop_win_ratio)
        self.query_one("#in-tp-vol", Input).value = "1" if cfg.momentum.stop_win_vol_ratio else "0"
