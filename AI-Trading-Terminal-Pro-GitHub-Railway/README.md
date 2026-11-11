# AI Trading Terminal Pro — Railway Starter (DEMO ONLY)

Starter project for GitHub → Railway. Contains a responsive HTML dashboard and FastAPI backend. The UI includes candlestick display, synthetic technical signals, approval/rejection and simulated orders. API also provides demo multi-account summaries, positions, history and simulated Close All.

**NOT LIVE:** No broker login, no MetaTrader connectivity, no actual AI model or verified fundamentals, no real orders. Demo state is in memory and resets when the server restarts. API has no authentication and MUST NOT be connected to live broker credentials or a trading gateway as-is.

## Local

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://localhost:8000. API docs: http://localhost:8000/docs.

## GitHub

Create an empty GitHub repository, upload all files in this folder to its root (or `git init`, `git add .`, `git commit -m "Initial demo"`, `git branch -M main`, `git remote add origin YOUR_REPO_URL`, `git push -u origin main`). Never commit `.env`, passwords, broker tokens, or account credentials.

## Railway

1. Railway → New Project → Deploy from GitHub Repo → select repository.
2. Railway detects `Dockerfile` and `railway.json`. No variables required for demo.
3. In Networking, generate a public domain.
4. Visit `https://YOUR-DOMAIN/api/status` (should show DEMO / DISCONNECTED), then open the root URL.

Railway pricing/free trial may change; check current limits in your account. This is not a production-grade deployment.

## Demo API

- `GET /api/status`
- `GET /api/candles/XAUUSD`
- `POST /api/analyze/XAUUSD`
- `POST /api/approve`
- `GET /api/orders`
- `GET /api/accounts`
- `GET /api/accounts/mt5-demo/summary`
- `GET /api/accounts/mt5-demo/positions`
- `GET /api/accounts/mt5-demo/history`
- `POST /api/accounts/mt5-demo/close-all-demo` with `{"confirm_demo":true,"side":"ALL"}`

## Before real broker trading

Implement authentication and per-user account authorization, encrypted secret management, PostgreSQL, audit logging, idempotent order commands, account-scoped approval, risk limits, quote freshness checks, secure authenticated EA bridge with broker execution acknowledgements, and paper/demo-account integration testing. MT4 and MT5 require their respective terminal/gateway; broker compatibility is not universal. Never treat an AI score as a calibrated win probability.

Android APK is not included in this starter; responsive web is available on Android browsers. A Capacitor wrapper can be built after the frontend is migrated to a suitable mobile project.
