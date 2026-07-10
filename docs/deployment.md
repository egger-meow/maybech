# Deployment Notes

## Runtime Mode Selection

Use `--mode simulation` (default), `--mode demo`, `--mode live_safe`, or
`--mode live_armed`. Demo requires `OKX_FLAG=1` and only `DEMO_OKX_*`; both live
modes require `OKX_FLAG=0` and only `OKX_*`. A mismatch fails preflight. Demo
and Live Armed require `MAYBECH_ARM_ORDERS=1`; Live Safe never arms orders.
Simulation may read an optional local JSON replay selected by
`MAYBECH_SIMULATION_CANDLES`. The file maps `INSTRUMENT:bar` keys to OKX-shaped
candle arrays; Simulation never falls back to OKX when a key or file is absent.

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
`NOTIFICATION_COOLDOWN_SECONDS` controls duplicate suppression for LINE and
Gmail lifecycle messages and defaults to 300. There is no standalone
market-price alert service; price logic belongs in persisted strategy/position
expressions.

### Future Demo / Real Dashboard Switch

The planned top-right environment selector is documentation-only in this
release. It must make the selected environment visually unmistakable:

- Demo selects only `DEMO_OKX_API_KEY`, `DEMO_OKX_API_SECRET`, and
  `DEMO_OKX_PASSPHRASE`, with `OKX_FLAG=1`.
- Real selects only `OKX_API_KEY`, `OKX_API_SECRET`, and `OKX_PASSPHRASE`, with
  `OKX_FLAG=0`.
- Switching the label or credential set must never set
  `MAYBECH_ARM_ORDERS=1`, pass preflight, enable a strategy, or open the entry
  gate. It is an environment choice, not one-click live arming.

Demo and Real must not share one SQLite database or one execution process.
Maybech's database lease and hashed OKX-account lease intentionally allow only
one mutating leader for a database/account scope. Side-by-side operation is
safe only as two explicitly separated deployments with different absolute
`MAYBECH_DB_PATH` values, credential scopes, ports, runtime locks, and one
leader per account. Until the UI can prove that separation, it should require a
restart to change environments rather than imply both are concurrently safe.

## Guarded Path To Real Money

There is intentionally no one-click transition from Simulation to real-money
execution. Use this sequence and stop whenever one stage is not independently
verified:

1. Keep `MAYBECH_ARM_ORDERS=0`. This means Maybech cannot place live orders,
   even if strategies are enabled in SQLite. Build and inspect strategies,
   logical-position rules, account risk limits, and audit evidence in Simulation.
2. Create a dedicated OKX demo API key first. Grant only the permissions needed
   for trading and inspection, never withdrawal permission. Configure the OKX
   IP whitelist for the machine that runs Maybech. Store demo credentials only
   in `DEMO_OKX_*`, select them with `OKX_FLAG=1`, and never expose secrets in
   the browser, docs, logs, commits, screenshots, or support messages.
3. Confirm the OKX account can trade derivatives and uses `net_mode`. Run the
   bounded demo lifecycle verifier and prove protected open, stop amendment,
   exact partial reduce, final close, restart catch-up, and zero residual
   positions/orders/algos.
4. Configure and review the persisted account risk envelope. Every enabled
   strategy needs explicit contract counts, a maximum entry-slippage fraction,
   and an enabled side-consistent absolute stop. Contract counts are OKX
   contracts, not base-asset quantities.
5. Start Demo with `--mode demo`; use `--mode live_armed` only after the preceding checks pass. Setting
   `MAYBECH_ARM_ORDERS=1` merely allows successful live preflight to arm order
   placement; it does not bypass credentials, account mode, risk, strategy,
   instrument, reconciliation, protection, lease, fill-catch-up, or private
   stream checks. A failed check aborts startup before services run.
6. Inspect `/runtime/preflight`, `/risk/limits`, `/risk/entries`, reconciliation,
   and the dashboard mode banner. Then separately enable reviewed strategies
   and explicitly confirm the entry gate. Once enabled, that choice persists
   and is restored automatically on a subsequent restart once the process
   re-arms; use `POST /risk/entries/kill` before stopping the process if a
   restart should come back with entries disabled. Reduce-only protection and
   exits remain independent of the entry gate.
7. Only after the complete demo path is repeatable should a dedicated
   production key be placed in `OKX_*` with `OKX_FLAG=0`. Repeat the read-only
   checks and bounded verifier at the smallest acceptable contract size before
   unattended strategy entry is considered.

Exchange stops limit risk but cannot guarantee a maximum realized loss during
price gaps, poor liquidity, exchange failure, or market-order slippage. Maybech
fails closed when required protection is missing or mismatched; that is not a
guarantee that a stop will fill exactly at its trigger price.

Before Demo or Live Armed, populate `metadata.order_size_contracts` and
`metadata.max_entry_slippage_pct` on each enabled strategy. Verify each size as
an OKX contract count rather than a base-asset quantity. The slippage value is a
decimal fraction greater than zero and no greater than `0.05`; it caps the FOK
limit price and is included in account-risk approval. The executor also checks
current OKX instrument precision.
Create the account risk envelope through `PUT /risk/limits` while running the
Simulation API, then inspect it with `GET /risk/limits`. Set operator-selected
positive limits for one order's USD notional, total gross USD exposure, and
maximum OKX cross leverage, plus a non-empty `allowed_instruments` list; keep
`enabled=false` until the values and searchable cached SWAP selections are
reviewed. Strategy targets outside this account boundary cannot pass live
preflight or per-entry approval.
The mutation requires `"confirm": true`, is rejected while strategy entries
are enabled, and writes a durable before/after audit in the same SQLite
transaction. These values are SQLite configuration and do not belong in `.env`.
Entry placement is a separate persisted control and defaults to disabled. Use
`POST /risk/entries/enable` with `{ "confirm": true }` only after reviewing the
risk envelope and successfully starting an armed live runtime. Once enabled, the
persisted state is restored automatically on subsequent restarts once the process
is armed again, so routine restarts do not resume-disable new entries. Simulation,
Live Safe, and offline enable attempts return a conflict instead of persisting
future activation. `POST /risk/entries/kill` with the same
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
OKX Spot account level `acctLv=1` is intentionally rejected even when API
authentication and `net_mode` succeed. Change the account to Futures,
Multi-currency margin, or Portfolio margin in OKX before attempting staged SWAP
verification; do not bypass this preflight check.
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

The verifier requires its confirmation to match `OKX_FLAG` and refuses
pre-existing nonzero BTC swap exposure. It uses minimum contract sizes,
persists sanitized SQLite audit evidence, disarms entry placement in `finally`,
and cleans verifier-owned orders, protection, and residual exposure. After the
demo proof passes, the same bounded path can stage production explicitly with
`--confirm-production-orders` and `OKX_FLAG=0`.
Production verification was completed with `0.02` BTC swap contracts, an exact
`0.01` partial reduce, and a final close. This command remains an explicit
operator tool; normal startup never invokes it and strategy entries remain
disabled unless separately enabled after armed runtime preflight.

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

### Runtime Roles

`src.runtime api` defaults to `--role combined`. This is the only role that
starts daemon services, acquires execution ownership, exposes live runtime
state, or accepts mutations. Exactly one combined process may own a database
and OKX account scope.

`src.runtime api --role replica` starts a read-only HTTP process without daemon
services, runtime locks, WebSocket events, or order capability. It rejects
execution `--mode`, `--live`, `--no-strategy`, and every non-read HTTP method. This role establishes
the safe read-replica boundary for future scaling; it is not another trading
worker.

SQLite remains a local-first database. Replica processes are supported only on
the same host or a filesystem with SQLite locking semantics. Multi-host
horizontal deployment still requires a shared transactional database and load
balancer routing that sends mutations and live runtime paths to the one
execution leader. Inspect the active contract through
`GET /runtime/capabilities`.

Replica-safe reads are persisted strategy, logical-position, risk, audit, and
public market/chart data. Account snapshots, reconciliation, service state,
runtime events, decisions/intents, and execution-ingestion status return `503`
on a replica so absent in-memory state cannot masquerade as an empty account.

Non-loopback binding fails unless `--allow-remote` is explicit and
`MAYBECH_API_TOKEN` is non-empty. Protected HTTP routes require
`Authorization: Bearer <token>`; the live WebSocket requires the same token as
the `token` query parameter. Health and capability discovery remain public.
Bearer authentication does not encrypt traffic, so terminate TLS at a trusted
reverse proxy or private tunnel and never expose plaintext control traffic to
the public internet.

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

Do not expose the API directly with public port forwarding. The API can control
real trading actions. For remote access, prefer a private tunnel such as VPN or
Tailscale. A reverse proxy must add TLS and an IP allowlist in addition to the
built-in bearer token.

## LINE Command Bot (Optional)

`POST /notifications/line/webhook` accepts inbound LINE Messaging API events and
executes a small fixed command grammar (`status`, `strategies`, `enable <id>`,
`disable <id>`) by calling the same FastAPI app's own routes in-process; every
existing safety gate (bearer auth on the routes it calls, the replica read-only
guard, strategy validation, optimistic-concurrency checks) applies identically
to a chat-triggered action as to one made through the dashboard. This is
disabled unless `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, and
`LINE_USER_ID` are all configured.

Every inbound sender is checked against `LINE_USER_ID` before any command
parses. A LINE Official Account can be added and messaged by anyone who finds
it, so this check, not the LINE console, is what keeps the bot single-operator.
Unrecognized senders get a generic "unauthorized" reply (rate-limited) and are
logged as `line_bot.unauthorized_attempt` audit events; never treat their
absence from replies as proof no one is probing the bot. Only run this webhook
against the combined execution leader. A replica instance rejects the route
outright (non-GET mutation) since it has no live account state to act on
anyway.

The route itself must stay unauthenticated by `MAYBECH_API_TOKEN` (LINE cannot
send that header); `X-Line-Signature` verification against `LINE_CHANNEL_SECRET`
is what authenticates the request instead. Because of that, exposing this one
route publicly is different from exposing the API generally (see "Remote
Access Safety" above). Restrict the tunnel to this single path; do not
forward the whole API.

Use ngrok when you do not own a domain. A plain foreground tunnel is enough for
manual testing, but its generated hostname can change; a LINE webhook URL must
be updated whenever that happens. For a webhook you want to register once, use
an ngrok reserved/static domain from your ngrok account and pass it with
`--url`.

Install ngrok, copy the account authtoken from the ngrok dashboard, and connect
the local agent:

```powershell
ngrok authtoken <your-ngrok-authtoken>
```

Start the Maybech API locally and expose only that local port through ngrok:

```powershell
uv run python -m src.runtime api --mode demo
ngrok http --domain=<your-ngrok-domain> 8000
```

For this workspace, you can put the reserved ngrok domain in `.env`:

```dotenv
MAYBECH_NGROK_DOMAIN=drop-down-promenade-carried.ngrok-free.dev
```

Start the backend from the repository root. The script keeps backend logs in
the current terminal and opens a separate PowerShell window for ngrok tunnel
logs:

```powershell
.\start_backend.ps1 -Mode demo
```

Use this URL in the LINE Developers console:

```text
https://drop-down-promenade-carried.ngrok-free.dev/notifications/line/webhook
```

Run the frontend in its own terminal so dashboard logs stay separate:

```powershell
cd frontend
npm run dev
```

To start only the backend without opening the ngrok tunnel:

```powershell
.\start_backend.ps1 -Mode demo -NoLineWebhookTunnel
```

Disable LINE auto-reply/greeting messages. Keep the main API bound to
`127.0.0.1`; ngrok supplies HTTPS on the public side while FastAPI still serves
the local HTTP backend. Do not expose the whole dashboard/API as a general
remote-control surface through this tunnel. The webhook route remains
unauthenticated by `MAYBECH_API_TOKEN`, but it still requires a valid
`X-Line-Signature` and the configured `LINE_USER_ID`.

For unattended Windows startup, create an ngrok config file that defines the
same tunnel and install ngrok as a service using that config:

```powershell
ngrok service install --config C:\ngrok\ngrok.yml
```

Use either the helper script or the ngrok service, not both for the same domain
and local port.
