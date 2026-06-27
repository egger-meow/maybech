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
`ExecutionFillService` uses the configured private OKX credentials to poll SWAP
fills. Without valid credentials its daemon status will show tick errors; it
never allocates fills whose order id is not linked to a Maybech logical unit.

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
