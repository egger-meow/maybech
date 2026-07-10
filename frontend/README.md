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

From the repository root, `.\start_frontend.ps1` performs the same startup and
can pass the local `.env` `MAYBECH_API_TOKEN` into `NEXT_PUBLIC_MAYBECH_API_TOKEN`
for a local-only dashboard session.

If the backend has `MAYBECH_API_TOKEN` configured, the dashboard prompts for it
before protected pages mount. For local-only development you may also set:

```powershell
$env:NEXT_PUBLIC_MAYBECH_API_TOKEN="<same value as MAYBECH_API_TOKEN>"
npm run dev
```

That value is exposed to the browser bundle, so do not use it for a dashboard
served to anyone else.

## Backend Dependency

Start the API from the repository root before using live data:

```powershell
uv run python run_api.py
```

Tracked API contracts are documented in `../docs/runtime-status.md`.

## Checks

```powershell
npm run contract
npm run lint
npm run build
```

`npm run contract` verifies that `../docs/openapi.json` and
`lib/generated/api-types.ts` match the backend FastAPI schema.
