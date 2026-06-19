# Repository Guidelines

## Project Structure & Module Organization

Maybech is a Python crypto trading, monitoring, and backtesting application. Runtime entry points live at the repository root: `main.py` and `run_services.py`. Core code is under `src/`, organized by responsibility: `exchange/` for OKX access, `trading/` for execution and risk, `strategies/` for strategy implementations, `backtesting/` for simulation and optimization, `data/` for market data and indicators, `notifications/` for LINE/email alerts, `daemon/` for services, and `ui/` or `monitor/` for Textual dashboards. Tests live in `tests/` and should mirror the behavior being changed. Documentation and images are in `docs/`; runtime sample/status data is in `data/`.

## Build, Test, and Development Commands

Use `uv` for local development.

```bash
uv venv
uv pip install -r requirements.txt
uv run python run_services.py
uv run pytest
uv run pytest --cov=src
```

`run_services.py` launches the Textual service console. `uv run pytest` runs the configured test suite from `tests/`; use `--cov=src` when touching shared strategy, exchange, daemon, or backtesting code.

## Coding Style & Naming Conventions

Use Python 3.11+ compatible syntax, 4-space indentation, and type hints for public functions or complex data flows. Prefer small modules with explicit responsibilities matching the existing `src/` package layout. Name files and modules in `snake_case.py`, classes in `PascalCase`, functions and variables in `snake_case`, and constants in `UPPER_SNAKE_CASE`. Keep configuration defaults in `src/config/` and avoid hard-coded credentials or account-specific values.

## Testing Guidelines

Tests use `pytest`, with discovery configured for `tests/test_*.py` in `pyproject.toml`. Name tests after expected behavior, for example `test_momentum_logic.py` or `test_daemon_service.py`. Add focused unit tests for strategy logic, risk calculations, config handling, and service state transitions. For exchange or notification changes, mock external OKX, LINE, and email calls unless the test is explicitly an integration test.

## Commit & Pull Request Guidelines

Recent history follows Conventional Commit prefixes such as `feat:`, `fix:`, and `docs:`. Keep commits short and imperative, for example `fix: restore default strategy config after tests`. Pull requests should include a clear summary, test results, linked issues when applicable, and screenshots for UI changes under `src/ui/` or `src/monitor/`.

## Security & Configuration Tips

Create local secrets from `.env.example` and never commit `.env`. Treat OKX API keys, LINE tokens, and notification targets as sensitive. Review changes to `src/config/strategy_params.json` and `src/config/notificator_config.json` carefully because they can alter live trading or alert behavior.
