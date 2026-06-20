# Maybech Frontend

Next.js dashboard for the local Maybech FastAPI runtime.

## Setup

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`.

The dashboard expects the backend API at `NEXT_PUBLIC_API_URL`. If unset, it
uses `http://127.0.0.1:8000`.

```powershell
$env:NEXT_PUBLIC_API_URL="http://127.0.0.1:8000"
npm run dev
```

## Backend Dependency

Start the API from the repository root before using live data:

```powershell
uv run python run_api.py
```

Tracked API contracts are documented in `../docs/runtime-status.md`.

## Checks

```powershell
npm run lint
npm run build
```
