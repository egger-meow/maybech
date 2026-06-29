# Deployment Notes

## Recommended Local Modes

Use the local Python virtual environment plus the Next.js dev server for normal
development and operation. That keeps secrets, logs, browser access, and
debugging straightforward while the API and dashboard are still evolving.

Use the runtime API when the browser dashboard, local tools, or always-on service should control the daemon:

```bash
uv run python -m src.runtime api
```

The API exposes service state at `http://127.0.0.1:8000/services` and live runtime events at `ws://127.0.0.1:8000/ws/events`.
It also exposes `GET /market/btc-regime`, `GET /strategy/decisions`, and `GET /position/intents` for frontend control surfaces.

Copy `.env.example` to `.env` for local operator settings. All backend stores
share `MAYBECH_DB_PATH` (`data/trades.db` by default). Use an absolute path for
service or scheduled-task deployments where the working directory may vary.
Production credentials use `OKX_API_KEY`, `OKX_API_SECRET`, and
`OKX_PASSPHRASE`. Demo credentials use the corresponding `DEMO_OKX_*` names.
`OKX_FLAG=1` must select only demo credentials; `OKX_FLAG=0` must select only
production credentials. Never copy real values into `.env.example` or docs.
Trading instruments, timeframe, signals, default close rules, and contract
counts are strategy data in SQLite; they are intentionally absent from `.env`.
Before `--live`, populate `metadata.order_size_contracts` and
`metadata.max_entry_slippage_pct` on each enabled strategy. Verify each size as
an OKX contract count rather than a base-asset quantity. The slippage value is a
decimal fraction greater than zero and no greater than `0.05`; it caps the FOK
limit price and is included in account-risk approval. The executor also checks
current OKX instrument precision.
Create the account risk envelope through `PUT /risk/limits` while running the
dry-run API, then inspect it with `GET /risk/limits`. Set operator-selected
positive limits for one order's USD notional, total gross USD exposure, and
maximum OKX cross leverage; keep `enabled=false` until the values are reviewed.
These values are SQLite configuration and do not belong in `.env`.
Entry placement is a separate persisted control and defaults to disabled. Use
`POST /risk/entries/enable` with `{ "confirm": true }` only after reviewing the
risk envelope and successfully starting an armed live runtime. Every live
startup first persists entries disabled and records an
`entry_control.startup_disabled` audit event, so a restart never resumes new
entries. Dry-run and offline enable attempts return a conflict instead of
persisting future activation. `POST /risk/entries/kill` with the same
confirmation disables new entries first, then requests cancellation of every
Maybech `pending_open` order it can resolve. Partial cancellation failures are
returned explicitly; the disabled state is not rolled back. Reduce-only
automatic and manual closes remain available in an armed live runtime.
Live startup first forces order placement off, then authenticates account
configuration and requires a derivatives-capable account in `net_mode`. It also
validates the enabled account risk envelope, every enabled strategy, and every
configured or actively managed SWAP instrument. Only a complete pass honors
`MAYBECH_ARM_ORDERS=1`; otherwise the
process exits before daemon services start. Inspect the successful report at
`GET /runtime/preflight` when using the API runtime.
Every runtime acquires a database lock; live startup also acquires one hashed
OKX-account lock. This prevents a dry process from consuming signal edges or
mutating the same SQLite state beside a live process.
On Windows these live under `%LOCALAPPDATA%\Maybech\locks`; other platforms use
`~/.maybech/locks`. Lock files contain only local process metadata and hashed
account scope. They are not configuration and should not be added to `.env` or
deleted to bypass a conflict. Inspect ownership through `GET /runtime/lease`.
The OS releases locks after a crash; orderly teardown disarms orders first.
`ExecutionFillService` uses the configured private OKX credentials to paginate
three-month SWAP fill history. Cursor checkpoints share `MAYBECH_DB_PATH` and
survive restarts. Without valid credentials its daemon status will show cursor
and tick errors; it never allocates fills whose order id is not linked to a
Maybech logical unit.
In live mode the same service must authenticate and receive a subscription
acknowledgement for the private OKX `orders/SWAP` WebSocket before startup can
arm orders. Production and demo WebSocket URLs are selected from `OKX_FLAG`.
The stream reconnects with bounded backoff and reports health through
`GET /execution/fills/status`. A disconnected stream or incomplete REST
catch-up blocks new entries without disabling automatic reduce-only closes.
It also requests cancellation once for linked active orders older than five
minutes. Position state changes only after OKX reports a terminal order state.

Run the bounded demo execution proof only with dedicated demo credentials and
an intentionally process-local arm:

```powershell
$env:MAYBECH_ARM_ORDERS='1'
uv run python scripts/verify_okx_demo_lifecycle.py --confirm-demo-orders
```

The verifier refuses `OKX_FLAG!=1` and pre-existing nonzero BTC swap exposure.
It uses minimum contract sizes, persists sanitized SQLite audit evidence,
disarms entry placement in `finally`, and cleans verifier-owned orders,
protection, and residual demo exposure. It is not a production verification
command.

The dashboard calls FastAPI from a separate local origin in development.
`MAYBECH_CORS_ORIGINS` defaults to `http://localhost:3000` and
`http://127.0.0.1:3000`. Add another explicit local origin if Next.js uses a
different port; do not use `*` while trading-control endpoints exist.

## Windows Auto-Start

For a personal PC, the simplest always-on setup is Windows Task Scheduler:

1. Create a task triggered "At log on" or "At startup".
2. Set the working directory to this repository.
3. Run either `uv run python -m src.runtime api` or
   `uv run python -m src.runtime services`.
4. Keep live trading disabled unless `.env`, account mode, and `MAYBECH_ARM_ORDERS=1` are intentionally configured.

Use `src.runtime api` if the Next.js frontend needs to manage services. Use
`src.runtime services` if only background alerts/signals are needed. The
root-level `run_api.py` and `run_services.py` wrappers remain supported for
existing local scripts.

## Docker Compose

Docker is not the default path for this repo right now. It is useful later when
the API is stable and you want repeatable always-on packaging:

```bash
docker compose up -d --build
docker compose logs -f maybech-api
docker compose down
```

The compose file binds `127.0.0.1:8000:8000`, so the API is local-only by default. It also persists `data/` and `logs/` through bind mounts.

## Remote Access Safety

Do not expose the API directly with public port forwarding. The API can enable/disable services and eventually may control trading actions. For remote access, prefer a private tunnel such as VPN or Tailscale. If a reverse proxy is added later, require authentication, TLS, and an IP allowlist before any trading-control endpoint is reachable.
