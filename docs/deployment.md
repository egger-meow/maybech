# Deployment Notes

## Recommended Local Modes

Use the local Python virtual environment plus the Next.js dev server for normal
development and operation. That keeps secrets, logs, browser access, and
debugging straightforward while the API and dashboard are still evolving.

Use the runtime API when the browser dashboard, local tools, or always-on service should control the daemon:

```bash
uv run python run_api.py
```

The API exposes service state at `http://127.0.0.1:8000/services` and live runtime events at `ws://127.0.0.1:8000/ws/events`.
It also exposes `GET /market/btc-regime`, `GET /strategy/decisions`, and `GET /position/intents` for frontend control surfaces.

## Windows Auto-Start

For a personal PC, the simplest always-on setup is Windows Task Scheduler:

1. Create a task triggered "At log on" or "At startup".
2. Set the working directory to this repository.
3. Run either `uv run python run_api.py` or `uv run python run_services.py`.
4. Keep live trading disabled unless `.env`, account mode, and `MAYBECH_ARM_ORDERS=1` are intentionally configured.

Use `run_api.py` if the Next.js frontend needs to manage services. Use `run_services.py` if only background alerts/signals are needed.

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
