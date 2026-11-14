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


## v4: MT4 / MT5 native technical indicator bridge (read-only)

All existing **demo routes** stay unchanged. New protected dashboard: `/live`.
This gateway does NOT execute orders, close positions, or accept broker passwords.
Real broker login stays inside licensed MetaTrader terminals on your Windows VPS.

### Railway environment variables
Create three Railway **Variables** (never put tokens in GitHub):

- `BRIDGE_TOKEN`: random unique secret 24+ chars, same value in EA Inputs.
- `DASHBOARD_TOKEN`: *different* random secret 24+ chars, used only in the /live dashboard login.
- `GEMINI_API_KEY`: Google AI Studio API key used for live AI read-only technical analysis.
- Optional `GEMINI_MODEL`: default `gemini-3.5-flash-lite`; change to a model enabled on your account.

Generate tokens locally in a secure password manager; never send login passwords or API keys in chat.

### On Windows VPS
1. Install the **official** MT4 or MT5 terminal provided by your broker. Log in **in the terminal**.
2. Open MetaEditor. Copy `bridges/MT4_IndicatorBridge.mq4` into MT4 `MQL4/Experts/` (or `.mq5` into MT5 `MQL5/Experts/`), then compile (F7). The EA sources have not yet been compiler-tested in MetaEditor.
3. In MetaTrader Tools > Options > Expert Advisors, check `Allow WebRequest for listed URL` and add:
   `https://ai-chart-wnalisis-production.up.railway.app` (MetaTrader requires this explicit allowlist).
4. Attach the read-only EA to a chart for each symbol you want to monitor. Input `ApiBase` and `BridgeToken` (same value as Railway). Run on a **demo account first**.
5. Open `https://ai-chart-wnalisis-production.up.railway.app/live`, enter `DASHBOARD_TOKEN`, and select a connected account/symbol/timeframe.
6. Native indicators read from the **last closed candle** (shift 1): EMA20/50/200, RSI14, MACD 12/26/9, ADX14 +/-DI, ATR14, Bollinger Bands 20/2. Available M1, M5, M15, M30, H1, H4, D1, W1, MN1 when bars exist.
7. Click **Analyze Broker Data with AI** to call Gemini. It never sends an order.

### Current limitations / safety
- Gateway data and latest history are **in memory** on Railway. Redeploys/restarts/replicas reset or desynchronize it. For production use a durable PostgreSQL store and one shared cache.
- Single shared bridge/dashboard token per environment; use per-account scoped credentials + authentication before multi-user production deployment.
- `/live` is protected for reading broker data; the HTML itself is public and requests a token. Do not store real passwords anywhere in source code.
- History is capped to recent terminal-visible orders/deals, and MT5 may report deals rather than complete round-trip trades.
- EA data arrival is approximately every 30 seconds; stale after 120 seconds. It is **not** a low-latency quote feed.
- There is **no fundamental news feed**: AI always marks fundamental analysis NOT_CONNECTED.
- No AI confidence means guaranteed probability; no live Close All or auto-entry endpoints.
- Only execute through actual MT4/MT5 terminals with separate controls after a security, risk and integration review.


## v5 — Mobile dashboard PIN, tabs, concise AI

The \`/live\` dashboard now uses **4-digit dashboard PIN authentication** and an 8-hour
Secure + HttpOnly + SameSite=Strict cookie. This is *read-only convenience
authentication* — a 4-digit PIN is weaker than a full password or MFA.

### Required Railway variables for the new login
- \`DASHBOARD_PIN\`: exactly **four digits**, e.g., a unique PIN that you choose in
  the Railway Variables UI. **Never enter it in GitHub or share it in chat.**
- Keep \`BRIDGE_TOKEN\` unchanged (existing MT4/MT5 EA installations use it).
- Keep \`GEMINI_API_KEY\` and \`GEMINI_MODEL\` unchanged.
- \`DASHBOARD_TOKEN\` continues to work as a long API Bearer token, but the new browser
  dashboard only accepts PIN; you may retain this secret for external read-only
  clients, or omit it if unused. Do not put it in the browser.
- Use exactly **one Railway web replica** for this MVP because PIN sessions,
  lockout counters and broker snapshots are stored in per-process memory.
  Restarting Railway logs users out and loses recent snapshots until the next EA push.

### UX changes
- Four top-level tabs: **Overview, Chart, AI Signal, History**.
- History uses five rows per page and Previous/Next buttons.
- AI returns one concise technical BUY/SELL bias + a **confidence alignment score**
  derived from native MetaTrader indicators. It is **not** a calibrated probability
  of profit. A low score (<60) is marked **Low alignment · No entry**.
- Gemini adds just one short rationale and one brief risk.
- No WAIT label, but no forced trade execution: contradictory signals can have a low
  alignment score and always stay read-only.
- Fundamental news remains **NOT_CONNECTED**.
- Existing demo dashboard and native MT4/MT5 EA data bridge stay intact.

### Security considerations
- 4-digit PIN is suitable only for a restricted read-only prototype; it is **not**
  sufficient authentication for future live trade operations. Before enabling
  trading, add per-user accounts, strong passwords/MFA, isolated account access,
  durable PostgreSQL sessions and distributed rate limiting.
- Login attempts are limited in-process (five failed attempts per remote address
  per 15 minutes and an additional global cap). Clearing the process resets those
  limits. HTTPS is required for the session cookie.
- All trading and bulk-close operations are disabled in /live.
